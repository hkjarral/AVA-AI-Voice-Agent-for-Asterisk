"""Keep submitted credential values out of FastAPI validation responses."""

from fastapi import HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute


class PrivateInputRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def private_handler(request):
            try:
                return await handler(request)
            except RequestValidationError as exc:
                # FastAPI includes the rejected input by default, sometimes the
                # entire body. Preserve field locations and Pydantic error codes.
                safe_types = {"extra_forbidden", "missing", "string_type", "bool_type", "int_type", "dict_type", "list_type"}
                errors = [
                    {"loc": error["loc"], "type": error["type"],
                     "msg": error["msg"] if error["type"] in safe_types else "Invalid field value"}
                    for error in exc.errors()
                ]
                raise HTTPException(422, detail=errors) from None

        return private_handler
