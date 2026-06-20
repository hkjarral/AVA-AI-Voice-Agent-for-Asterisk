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

# Durable, root-only copy of the one-time admin password (LOW-U2). Stdout/log
# output can be rotated or forwarded away, locking the operator out; this file is
# the recoverable copy. Lives next to users.json under config/ (a gitignored data dir).
FIRST_RUN_PASSWORD_PATH = os.path.join(os.path.dirname(USERS_PATH), ".first-run-password")

def _write_first_run_password_file(password: str) -> None:
    """Write the one-time admin password to a 0600 file. Best-effort; never raises.

    Only called on the first-run/rotation path, so the file exists only while a
    one-time password is outstanding (the operator deletes it after changing it).
    """
    try:
        # O_CREAT|O_TRUNC with mode 0o600 so the file is owner-only from creation.
        # The mode arg is ignored for an existing file, so fchmod the fd to 0o600
        # BEFORE writing — closing the window where an existing wider-perm file is
        # truncated/written before perms are tightened. O_NOFOLLOW refuses to
        # follow a symlink planted at the path.
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        flags |= getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(
            FIRST_RUN_PASSWORD_PATH,
            flags,
            0o600,
        )
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(
                f"{password}\n"
                "One-time admin password — change it at first login, then delete this file.\n"
            )
    except Exception:
        # Stdout/log message remains the primary channel; the file is a bonus.
        pass

def ensure_default_user() -> "str | None":
    """Create or rotate the initial admin user with a random one-time password.

    Returns the plaintext password whenever it (re)generated one, else None.

    Fresh install (no users.json):
        Creates config/users.json atomically at mode 0o600 with a random
        secrets.token_urlsafe(16) password and must_change_password=True.

    Upgraded install (users.json exists but admin/admin still works):
        Rotates the password to a new random value and sets must_change_password=True,
        so legacy defaults cannot be exploited on upgraded installs.

    Already secured (users.json exists and admin/admin does not verify):
        Returns None — no action.
    """
    os.makedirs(os.path.dirname(USERS_PATH), exist_ok=True)

    # --- Fresh install path: create atomically so concurrent first-run processes
    # cannot race and the file lands at mode 0o600 in one syscall.
    try:
        fd = os.open(USERS_PATH, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        # File exists — fall through to the upgrade-rotation check below.
        pass
    else:
        password = secrets.token_urlsafe(16)
        users = {
            "admin": {
                "username": "admin",
                "hashed_password": get_password_hash(password),
                "disabled": False,
                "must_change_password": True,
            }
        }
        with os.fdopen(fd, "w") as f:
            json.dump(users, f, indent=2)
        _write_first_run_password_file(password)
        return password

    # --- Existing-file path: rotate if the legacy admin/admin default is still in place.
    with open(USERS_PATH, "r") as f:
        users = json.load(f)

    admin = users.get("admin", {})
    if admin and verify_password("admin", admin.get("hashed_password", "")):
        # Legacy default still active — regenerate and save.
        password = secrets.token_urlsafe(16)
        admin["hashed_password"] = get_password_hash(password)
        admin["must_change_password"] = True
        users["admin"] = admin
        with open(USERS_PATH, "w") as f:
            json.dump(users, f, indent=2)
        _write_first_run_password_file(password)
        return password

    return None


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
