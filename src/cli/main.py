
"""CLI interface for Pulse administration."""

from __future__ import annotations

import asyncio
import typer
from rich.console import Console
from rich.table import Table

from src.config.database import init_db, close_db, async_session_factory
from src.models.user import User

app = typer.Typer(help="Pulse Monitoring Platform CLI")
console = Console()


@app.command()
def init():
    """Initialize database tables."""
    async def _init():
        await init_db()
        console.print("[green]Database initialized successfully[/green]")
        await close_db()
    asyncio.run(_init())


@app.command()
def create_user(
    email: str = typer.Option(..., help="User email"),
    username: str = typer.Option(..., help="Username"),
    password: str = typer.Option(..., help="Password"),
    admin: bool = typer.Option(False, help="Make superuser"),
):
    """Create a new user."""
    from src.services.auth_service import AuthService

    async def _create():
        await init_db()
        async with async_session_factory() as session:
            user = User(
                email=email.lower(),
                username=username.lower(),
                hashed_password=AuthService.hash_password(password),
                is_superuser=admin,
                is_verified=True,
            )
            session.add(user)
            await session.commit()
            console.print(f"[green]Created user {username} ({email})[/green]")
        await close_db()
    asyncio.run(_create())


@app.command()
def list_users():
    """List all users."""

    async def _list():
        await init_db()
        async with async_session_factory() as session:
            from sqlalchemy import select
            result = await session.execute(select(User))
            users = result.scalars().all()
            table = Table(title="Pulse Users")
            table.add_column("ID", style="dim")
            table.add_column("Username")
            table.add_column("Email")
            table.add_column("Plan")
            table.add_column("Active")
            for u in users:
                table.add_row(u.id[:8]+"...", u.username, u.email, u.plan, str(u.is_active))
            console.print(table)
        await close_db()
    asyncio.run(_list())


@app.command()
def create_admin(
    email: str = typer.Option("admin@pulse.local"),
    username: str = typer.Option("admin"),
    password: str = typer.Option("changeme123"),
):
    """Create default admin user."""
    create_user(email=email, username=username, password=password, admin=True)


@app.command()
def serve(
    host: str = typer.Option("0.0.0.0"),
    port: int = typer.Option(8000),
    reload: bool = typer.Option(False),
):
    """Run the development server."""
    import uvicorn
    uvicorn.run("src.main:app", host=host, port=port, reload=reload)


if __name__ == "__main__":
    app()
