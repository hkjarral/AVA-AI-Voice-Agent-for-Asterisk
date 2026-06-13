import os
import json
import secrets
from datetime import datetime, timedelta
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from pydantic import BaseModel
from passlib.context import CryptContext
from jose import JWTError, jwt
from settings import USERS_PATH

# Paths exempt from the must_change_password 403 gate (router mounted at prefix="/api/auth" in main.py)
_EXEMPT_PATHS = {"/api/auth/change-password", "/api/auth/me"}

# Configuration
DEFAULT_DEV_SECRET = "dev-secret-key-change-in-prod"
PLACEHOLDER_SECRETS = {
    "",
    "change-me-please",
    "changeme",
    DEFAULT_DEV_SECRET,
}

_raw_secret = (os.getenv("JWT_SECRET", "") or "").strip()
SECRET_KEY = _raw_secret or DEFAULT_DEV_SECRET
USING_PLACEHOLDER_SECRET = SECRET_KEY in PLACEHOLDER_SECRETS
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24  # 24 hours

# Password hashing
pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")

router = APIRouter()

class Token(BaseModel):
    access_token: str
    token_type: str
    must_change_password: bool = False

class TokenData(BaseModel):
    username: Optional[str] = None

class User(BaseModel):
    username: str
    disabled: Optional[bool] = None
    must_change_password: Optional[bool] = False

class UserInDB(User):
    hashed_password: str
    must_change_password: Optional[bool] = False

class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str

# --- Helper Functions ---

def get_password_hash(password):
    return pwd_context.hash(password)

def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)

def ensure_default_user() -> "str | None":
    """Create the initial admin user with a random one-time password.

    Returns the plaintext password on first creation (for log output), else None.
    Idempotent: if the users file already exists, does nothing and returns None.
    """
    if os.path.exists(USERS_PATH):
        return None
    password = secrets.token_urlsafe(16)
    users = {
        "admin": {
            "username": "admin",
            "hashed_password": get_password_hash(password),
            "disabled": False,
            "must_change_password": True,
        }
    }
    os.makedirs(os.path.dirname(USERS_PATH), exist_ok=True)
    with open(USERS_PATH, "w") as f:
        json.dump(users, f, indent=2)
    try:
        os.chmod(USERS_PATH, 0o600)
    except OSError:
        pass
    return password


def load_users():
    """Load users from the users file.

    If the file does not yet exist, ensure_default_user() must be called first
    (main.py does this at startup). Callers that reach here before startup
    completes get an empty dict, which causes a 401 rather than a crash.
    """
    if not os.path.exists(USERS_PATH):
        return {}

    with open(USERS_PATH, "r") as f:
        return json.load(f)

def save_users(users):
    os.makedirs(os.path.dirname(USERS_PATH), exist_ok=True)
    with open(USERS_PATH, "w") as f:
        json.dump(users, f, indent=2)

def get_user(username: str):
    users = load_users()
    if username in users:
        user_dict = users[username]
        return UserInDB(**user_dict)
    return None

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=15)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

async def get_current_user(request: Request, token: str = Depends(oauth2_scheme)):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise credentials_exception
        token_data = TokenData(username=username)
    except JWTError:
        raise credentials_exception

    user = get_user(token_data.username)
    if user is None:
        raise credentials_exception

    # 403 gate: block protected endpoints until the user changes their one-time password.
    # _EXEMPT_PATHS allows the user to reach change-password and me to complete the rotation.
    if user.must_change_password and request.url.path not in _EXEMPT_PATHS:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Password change required before using the API",
        )

    return user

# --- Routes ---

@router.post("/login", response_model=Token)
async def login_for_access_token(form_data: OAuth2PasswordRequestForm = Depends()):
    user = get_user(form_data.username)
    if not user or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    # Check if user needs to change password
    users = load_users()
    user_dict = users.get(user.username, {})
    must_change = user_dict.get("must_change_password", False)
    
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": user.username}, expires_delta=access_token_expires
    )
    return {"access_token": access_token, "token_type": "bearer", "must_change_password": must_change}

@router.post("/change-password")
async def change_password(
    request: ChangePasswordRequest,
    current_user: User = Depends(get_current_user)
):
    users = load_users()
    user_dict = users.get(current_user.username)
    
    if not user_dict:
        raise HTTPException(status_code=404, detail="User not found")
        
    if not verify_password(request.old_password, user_dict["hashed_password"]):
        raise HTTPException(status_code=400, detail="Incorrect old password")
        
    # Update password and clear must_change_password flag
    users[current_user.username]["hashed_password"] = get_password_hash(request.new_password)
    users[current_user.username]["must_change_password"] = False
    save_users(users)
    
    return {"status": "success", "message": "Password updated successfully"}

@router.get("/me", response_model=User)
async def read_users_me(current_user: User = Depends(get_current_user)):
    return current_user
