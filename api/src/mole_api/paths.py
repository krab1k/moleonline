"""Paths as the former C# API (ASP.NET) matched them, the web and scripts use them: endpoint names in any case (the web asks
for /Compinfo/...), a trailing slash ignored (the web uploads to /Init/)."""

from fastapi.routing import APIRoute


class AspNetPaths:
    def __init__(self, app):
        self.app = app
        self.names: dict[str, str] | None = None  # lower case -> the name in the routes, from the app when first asked

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            if self.names is None:
                self.names = {segment.lower(): segment for route in scope["app"].routes if isinstance(route, APIRoute)
                              for segment in route.path.split("/") if segment and not segment.startswith("{")}
            # the path after the root path (--root-path /api: uvicorn passes /api/Status/x, the routes see /Status/x)
            root = scope.get("root_path", "")
            path = scope["path"].removeprefix(root)
            path = path.rstrip("/") or "/"
            scope = {**scope, "path": root + "/".join(self.names.get(segment.lower(), segment) for segment in path.split("/"))}
        await self.app(scope, receive, send)
