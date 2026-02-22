"""Main FastAPI application."""

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from prometheus_fastapi_instrumentator import Instrumentator

from chatbot_ai_system import __version__
from chatbot_ai_system.config import get_settings
from chatbot_ai_system.database.redis import redis_client

from .multimodal_routes import router as multimodal_router
from .plugin_routes import router as plugin_router
from .routes import router

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    settings = get_settings()

    app = FastAPI(
        title="Chatbot AI System",
        description="Production-grade AI chatbot platform with multi-provider LLM support",
        version=__version__,
        docs_url="/docs",
        redoc_url="/redoc",
    )

    # Configure CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Include routes
    app.include_router(router)
    app.include_router(multimodal_router)  # Phase 5.0: Upload, Voice
    app.include_router(plugin_router)  # Plugin management

    # Initialize Prometheus Instrumentation
    Instrumentator().instrument(app).expose(app)

    @app.on_event("startup")
    async def startup_event():
        logger.info(f"Starting Chatbot AI System v{__version__}")
        logger.info(f"Debug mode: {settings.debug}")
        logger.info(f"Default LLM provider: {settings.default_llm_provider}")

        # Initialize Redis
        await redis_client.connect(settings.redis_url)

        # Initialize and register MCP clients
        import os

        from chatbot_ai_system.config.mcp_server_config import get_mcp_servers
        from chatbot_ai_system.tools import registry
        from chatbot_ai_system.tools.mcp_client import MCPClient

        # Load MCP servers from configuration
        servers = await get_mcp_servers()
        logger.info(f"Loading {len(servers)} MCP servers...")

        for server_config in servers:
            try:
                # Check for required env vars again (safety check)
                missing_vars = [
                    var
                    for var in server_config.required_env_vars
                    if not server_config.env_vars.get(var) and not os.environ.get(var)
                ]
                if missing_vars:
                    logger.warning(
                        f"Skipping MCP server {server_config.name}: Missing required environment variables: {', '.join(missing_vars)}"
                    )
                    continue

                client = MCPClient(
                    name=server_config.name,
                    command=server_config.command,
                    args=server_config.args,
                    env=server_config.env_vars or os.environ.copy(),
                )
                registry.register_mcp_client(client)
                logger.info(f"Registered MCP server: {server_config.name}")
            except Exception as e:
                logger.error(f"Failed to register MCP server {server_config.name}: {e}")

        # Refresh tools
        try:
            await registry.refresh_remote_tools()
            logger.info("MCP servers registered and tools refreshed")
        except Exception as e:
            logger.error(f"Error refreshing MCP tools: {e}")

    @app.on_event("shutdown")
    async def shutdown_event():
        logger.info("Shutting down Chatbot AI System")

        # Cleanup MCP clients
        # (The registry or clients should ideally handle this, but for now we rely on process termination
        # or we could add a cleanup method to registry)
        # We might want to explicitly close them if we had a reference, but they are in the registry's list.
        # Ideally, we'd add a close_all method to registry.

        # Cleanup providers
        from .routes import _providers

        for provider in _providers.values():
            if hasattr(provider, "close"):
                await provider.close()

        # Close Redis
        await redis_client.close()

    return app


# Create app instance
app = create_app()


if __name__ == "__main__":
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "chatbot_ai_system.server.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
    )
