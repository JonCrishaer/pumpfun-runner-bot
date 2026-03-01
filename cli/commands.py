"""
CLI Interface for Pump.fun Trading Bot
Provides commands for system control, position management, and monitoring
"""

import asyncio
import click
import json
import os
import sys
from datetime import datetime
from typing import Dict, List, Optional
from dataclasses import dataclass
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.layout import Layout
from rich.live import Live
from rich.spinner import Spinner
from rich.progress import Progress, SpinnerColumn, TextColumn
import aiohttp
import requests

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

console = Console()

# Configuration
DEFAULT_API_URL = "http://localhost:8080"
API_URL = os.getenv('PUMPFUN_API_URL', DEFAULT_API_URL)


class TradingClient:
    """HTTP client for trading API"""
    
    def __init__(self, base_url: str = API_URL):
        self.base_url = base_url
        self.session: Optional[aiohttp.ClientSession] = None
    
    async def _get_session(self) -> aiohttp.ClientSession:
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession()
        return self.session
    
    async def get_status(self) -> Dict:
        """Get system status"""
        try:
            session = await self._get_session()
            async with session.get(f"{self.base_url}/api/status") as resp:
                if resp.status == 200:
                    return await resp.json()
                return {'error': f'HTTP {resp.status}'}
        except Exception as e:
            return {'error': str(e)}
    
    async def get_positions(self) -> List[Dict]:
        """Get all positions"""
        try:
            session = await self._get_session()
            async with session.get(f"{self.base_url}/api/positions") as resp:
                if resp.status == 200:
                    return await resp.json()
                return []
        except Exception as e:
            console.print(f"[red]Error: {e}[/red]")
            return []
    
    async def buy(self, token: str, amount: float, slippage: float = 1.0) -> Dict:
        """Execute buy order"""
        try:
            session = await self._get_session()
            payload = {
                'token': token,
                'amount': amount,
                'slippage': slippage
            }
            async with session.post(f"{self.base_url}/api/buy", json=payload) as resp:
                if resp.status == 200:
                    return await resp.json()
                return {'error': f'HTTP {resp.status}', 'details': await resp.text()}
        except Exception as e:
            return {'error': str(e)}
    
    async def sell(self, token: str, percentage: float, slippage: float = 1.0) -> Dict:
        """Execute sell order"""
        try:
            session = await self._get_session()
            payload = {
                'token': token,
                'percentage': percentage,
                'slippage': slippage
            }
            async with session.post(f"{self.base_url}/api/sell", json=payload) as resp:
                if resp.status == 200:
                    return await resp.json()
                return {'error': f'HTTP {resp.status}', 'details': await resp.text()}
        except Exception as e:
            return {'error': str(e)}
    
    async def pause(self) -> Dict:
        """Pause trading"""
        try:
            session = await self._get_session()
            async with session.post(f"{self.base_url}/api/pause") as resp:
                if resp.status == 200:
                    return await resp.json()
                return {'error': f'HTTP {resp.status}'}
        except Exception as e:
            return {'error': str(e)}
    
    async def resume(self) -> Dict:
        """Resume trading"""
        try:
            session = await self._get_session()
            async with session.post(f"{self.base_url}/api/resume") as resp:
                if resp.status == 200:
                    return await resp.json()
                return {'error': f'HTTP {resp.status}'}
        except Exception as e:
            return {'error': str(e)}
    
    async def shutdown(self) -> Dict:
        """Shutdown system"""
        try:
            session = await self._get_session()
            async with session.post(f"{self.base_url}/api/shutdown") as resp:
                if resp.status == 200:
                    return await resp.json()
                return {'error': f'HTTP {resp.status}'}
        except Exception as e:
            return {'error': str(e)}
    
    async def get_health(self) -> Dict:
        """Get health status"""
        try:
            session = await self._get_session()
            async with session.get(f"{self.base_url}/api/health") as resp:
                if resp.status == 200:
                    return await resp.json()
                return {'error': f'HTTP {resp.status}'}
        except Exception as e:
            return {'error': str(e)}
    
    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()


# Click CLI Group
@click.group()
@click.option('--api-url', default=API_URL, help='API base URL')
@click.pass_context
def cli(ctx, api_url):
    """Pump.fun Trading Bot CLI"""
    ctx.ensure_object(dict)
    ctx.obj['client'] = TradingClient(api_url)
    ctx.obj['api_url'] = api_url


@cli.command()
@click.pass_context
def status(ctx):
    """Show system status and active positions"""
    client = ctx.obj['client']
    
    with console.status("[bold green]Fetching status..."):
        status_data = asyncio.run(client.get_status())
    
    if 'error' in status_data:
        console.print(f"[red]Error: {status_data['error']}[/red]")
        return
    
    # Create layout
    layout = Layout()
    
    # System status panel
    system_info = status_data.get('system', {})
    status_color = "green" if system_info.get('status') == 'running' else "red"
    
    system_panel = Panel(
        f"""
[bold]Status:[/bold] [{status_color}]{system_info.get('status', 'unknown').upper()}[/{status_color}]
[bold]Uptime:[/bold] {system_info.get('uptime', 'N/A')}
[bold]Version:[/bold] {system_info.get('version', 'N/A')}
[bold]Mode:[/bold] {system_info.get('mode', 'N/A')}
        """,
        title="System",
        border_style="blue"
    )
    
    # Wallet panel
    wallet_info = status_data.get('wallet', {})
    wallet_panel = Panel(
        f"""
[bold]Balance:[/bold] {wallet_info.get('balance_sol', 0):.4f} SOL
[bold]Address:[/bold] {wallet_info.get('address', 'N/A')[:20]}...
[bold]Pending TX:[/bold] {wallet_info.get('pending_tx', 0)}
        """,
        title="Wallet",
        border_style="green"
    )
    
    # Trading panel
    trading_info = status_data.get('trading', {})
    trading_status = "green" if trading_info.get('enabled') else "red"
    
    trading_panel = Panel(
        f"""
[bold]Trading:[/bold] [{trading_status}]{'ENABLED' if trading_info.get('enabled') else 'PAUSED'}[/{trading_status}]
[bold]Active Positions:[/bold] {trading_info.get('active_positions', 0)}
[bold]24h Trades:[/bold] {trading_info.get('trades_24h', 0)}
[bold]24h P&L:[/bold] {trading_info.get('pnl_24h', 0):+.2f}%
        """,
        title="Trading",
        border_style="yellow"
    )
    
    # Display panels
    console.print(system_panel)
    console.print(wallet_panel)
    console.print(trading_panel)
    
    # Active positions table
    positions = status_data.get('positions', [])
    if positions:
        table = Table(title="Active Positions")
        table.add_column("Symbol", style="cyan")
        table.add_column("Entry", justify="right")
        table.add_column("Current", justify="right")
        table.add_column("P&L %", justify="right")
        table.add_column("P&L USD", justify="right")
        table.add_column("Duration")
        
        for pos in positions:
            pnl_color = "green" if pos.get('pnl_pct', 0) > 0 else "red"
            table.add_row(
                pos.get('symbol', 'N/A'),
                f"${pos.get('entry_price', 0):.6f}",
                f"${pos.get('current_price', 0):.6f}",
                f"[{pnl_color}]{pos.get('pnl_pct', 0):+.2f}%[/{pnl_color}]",
                f"[{pnl_color}]${pos.get('pnl_usd', 0):+.2f}[/{pnl_color}]",
                pos.get('duration', 'N/A')
            )
        
        console.print(table)
    
    asyncio.run(client.close())


@cli.command()
@click.pass_context
def positions(ctx):
    """List all positions with P&L"""
    client = ctx.obj['client']
    
    with console.status("[bold green]Fetching positions..."):
        positions_data = asyncio.run(client.get_positions())
    
    if not positions_data:
        console.print("[yellow]No active positions[/yellow]")
        asyncio.run(client.close())
        return
    
    table = Table(title="All Positions")
    table.add_column("Symbol", style="cyan", no_wrap=True)
    table.add_column("Address", style="dim", width=20)
    table.add_column("Entry Price", justify="right")
    table.add_column("Current", justify="right")
    table.add_column("Quantity", justify="right")
    table.add_column("Value", justify="right")
    table.add_column("P&L %", justify="right")
    table.add_column("P&L USD", justify="right")
    table.add_column("Status")
    
    total_pnl = 0
    total_value = 0
    
    for pos in positions_data:
        pnl_pct = pos.get('pnl_pct', 0)
        pnl_usd = pos.get('pnl_usd', 0)
        pnl_color = "green" if pnl_pct > 0 else "red"
        
        total_pnl += pnl_usd
        total_value += pos.get('value_usd', 0)
        
        table.add_row(
            pos.get('symbol', 'N/A'),
            pos.get('address', 'N/A')[:18] + "...",
            f"${pos.get('entry_price', 0):.8f}",
            f"${pos.get('current_price', 0):.8f}",
            f"{pos.get('quantity', 0):,.0f}",
            f"${pos.get('value_usd', 0):,.2f}",
            f"[{pnl_color}]{pnl_pct:+.2f}%[/{pnl_color}]",
            f"[{pnl_color}]${pnl_usd:+.2f}[/{pnl_color}]",
            pos.get('status', 'N/A')
        )
    
    console.print(table)
    
    # Summary
    pnl_color = "green" if total_pnl > 0 else "red"
    console.print(f"\n[bold]Total Value:[/bold] ${total_value:,.2f}")
    console.print(f"[bold]Total P&L:[/bold] [{pnl_color}]${total_pnl:+.2f}[/{pnl_color}]")
    
    asyncio.run(client.close())


@cli.command()
@click.argument('token')
@click.argument('amount', type=float)
@click.option('--slippage', default=1.0, help='Slippage tolerance %')
@click.pass_context
def buy(ctx, token, amount, slippage):
    """Execute manual buy order"""
    client = ctx.obj['client']
    
    # Confirmation
    console.print(f"[yellow]Buy {amount} USD worth of {token}?[/yellow]")
    console.print(f"Slippage: {slippage}%")
    
    if not click.confirm("Proceed?"):
        console.print("[yellow]Cancelled[/yellow]")
        return
    
    with console.status(f"[bold green]Buying {token}..."):
        result = asyncio.run(client.buy(token, amount, slippage))
    
    if 'error' in result:
        console.print(f"[red]Error: {result['error']}[/red]")
        if 'details' in result:
            console.print(f"[dim]{result['details']}[/dim]")
    else:
        console.print(f"[green]✓ Buy order executed![/green]")
        console.print(f"Transaction: {result.get('tx_hash', 'N/A')}")
        console.print(f"Amount: {result.get('amount', 0):.6f} {token}")
        console.print(f"Price: ${result.get('price', 0):.8f}")
        console.print(f"Total: ${result.get('total', 0):.2f}")
    
    asyncio.run(client.close())


@cli.command()
@click.argument('token')
@click.argument('percentage', type=float)
@click.option('--slippage', default=1.0, help='Slippage tolerance %')
@click.pass_context
def sell(ctx, token, percentage, slippage):
    """Execute manual sell order"""
    client = ctx.obj['client']
    
    if percentage <= 0 or percentage > 100:
        console.print("[red]Percentage must be between 0 and 100[/red]")
        return
    
    # Confirmation
    console.print(f"[yellow]Sell {percentage}% of {token} position?[/yellow]")
    console.print(f"Slippage: {slippage}%")
    
    if not click.confirm("Proceed?"):
        console.print("[yellow]Cancelled[/yellow]")
        return
    
    with console.status(f"[bold green]Selling {token}..."):
        result = asyncio.run(client.sell(token, percentage, slippage))
    
    if 'error' in result:
        console.print(f"[red]Error: {result['error']}[/red]")
        if 'details' in result:
            console.print(f"[dim]{result['details']}[/dim]")
    else:
        console.print(f"[green]✓ Sell order executed![/green]")
        console.print(f"Transaction: {result.get('tx_hash', 'N/A')}")
        console.print(f"Amount Sold: {result.get('amount', 0):.6f} {token}")
        console.print(f"Price: ${result.get('price', 0):.8f}")
        console.print(f"Received: ${result.get('received', 0):.2f}")
        console.print(f"P&L: {result.get('pnl_pct', 0):+.2f}%")
    
    asyncio.run(client.close())


@cli.command()
@click.pass_context
def pause(ctx):
    """Pause new trading entries"""
    client = ctx.obj['client']
    
    if not click.confirm("Pause all new trading entries?"):
        console.print("[yellow]Cancelled[/yellow]")
        return
    
    with console.status("[bold yellow]Pausing trading..."):
        result = asyncio.run(client.pause())
    
    if 'error' in result:
        console.print(f"[red]Error: {result['error']}[/red]")
    else:
        console.print(f"[yellow]⏸ Trading paused[/yellow]")
        console.print(f"Active positions will continue to be monitored")
    
    asyncio.run(client.close())


@cli.command()
@click.pass_context
def resume(ctx):
    """Resume trading"""
    client = ctx.obj['client']
    
    with console.status("[bold green]Resuming trading..."):
        result = asyncio.run(client.resume())
    
    if 'error' in result:
        console.print(f"[red]Error: {result['error']}[/red]")
    else:
        console.print(f"[green]▶ Trading resumed[/green]")
    
    asyncio.run(client.close())


@cli.command()
@click.option('--force', is_flag=True, help='Force shutdown without confirmation')
@click.pass_context
def shutdown(ctx, force):
    """Graceful system shutdown"""
    client = ctx.obj['client']
    
    if not force:
        console.print("[red]WARNING: This will shut down the trading bot![/red]")
        console.print("All positions will remain open.")
        
        if not click.confirm("Are you sure?"):
            console.print("[yellow]Cancelled[/yellow]")
            return
    
    with console.status("[bold red]Shutting down..."):
        result = asyncio.run(client.shutdown())
    
    if 'error' in result:
        console.print(f"[red]Error: {result['error']}[/red]")
    else:
        console.print(f"[green]✓ System shutdown initiated[/green]")
        console.print(f"Message: {result.get('message', 'N/A')}")
    
    asyncio.run(client.close())


@cli.command()
@click.pass_context
def health(ctx):
    """Show detailed health check status"""
    client = ctx.obj['client']
    
    with console.status("[bold green]Checking health..."):
        health_data = asyncio.run(client.get_health())
    
    if 'error' in health_data:
        console.print(f"[red]Error: {health_data['error']}[/red]")
        asyncio.run(client.close())
        return
    
    # Overall status
    overall = health_data.get('status', 'unknown')
    status_color = {
        'healthy': 'green',
        'warning': 'yellow',
        'critical': 'red',
        'unknown': 'dim'
    }.get(overall, 'dim')
    
    console.print(Panel(
        f"[bold {status_color}]{overall.upper()}[/bold {status_color}]",
        title="Overall Status",
        border_style=status_color
    ))
    
    # Individual checks
    checks = health_data.get('checks', {})
    if checks:
        table = Table(title="Health Checks")
        table.add_column("Check", style="cyan")
        table.add_column("Status")
        table.add_column("Message")
        table.add_column("Response", justify="right")
        table.add_column("Last Check")
        
        for name, check in checks.items():
            status = check.get('status', 'unknown')
            color = {
                'healthy': 'green',
                'warning': 'yellow',
                'critical': 'red'
            }.get(status, 'dim')
            
            table.add_row(
                name,
                f"[{color}]{status.upper()}[/{color}]",
                check.get('message', 'N/A')[:40],
                f"{check.get('response_time_ms', 0):.0f}ms",
                check.get('timestamp', 'N/A')[:19]
            )
        
        console.print(table)
    
    # Summary
    summary = health_data.get('summary', {})
    console.print(f"\n[bold]Summary:[/bold] " +
                  f"[green]{summary.get('healthy', 0)} healthy[/green] | " +
                  f"[yellow]{summary.get('warning', 0)} warning[/yellow] | " +
                  f"[red]{summary.get('critical', 0)} critical[/red]")
    
    asyncio.run(client.close())


@cli.command()
@click.pass_context
def monitor(ctx):
    """Live monitoring dashboard"""
    client = ctx.obj['client']
    
    console.print("[bold green]Starting live monitor... Press Ctrl+C to exit[/bold green]")
    
    try:
        while True:
            # Clear screen (works on Unix-like systems)
            os.system('clear' if os.name != 'nt' else 'cls')
            
            # Get status
            status_data = asyncio.run(client.get_status())
            
            # Header
            now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            console.print(f"[bold]Pump.fun Trader Monitor[/bold] - {now}")
            console.print("─" * 60)
            
            if 'error' in status_data:
                console.print(f"[red]Error: {status_data['error']}[/red]")
            else:
                # Quick stats
                system = status_data.get('system', {})
                trading = status_data.get('trading', {})
                wallet = status_data.get('wallet', {})
                
                status_emoji = "🟢" if system.get('status') == 'running' else "🔴"
                trading_emoji = "▶" if trading.get('enabled') else "⏸"
                
                console.print(f"{status_emoji} System: {system.get('status', 'N/A').upper()}")
                console.print(f"{trading_emoji} Trading: {'ENABLED' if trading.get('enabled') else 'PAUSED'}")
                console.print(f"💰 Balance: {wallet.get('balance_sol', 0):.4f} SOL")
                console.print(f"📊 Positions: {trading.get('active_positions', 0)}")
                console.print(f"📈 24h P&L: {trading.get('pnl_24h', 0):+.2f}%")
            
            console.print("─" * 60)
            console.print("[dim]Refreshing every 5 seconds...[/dim]")
            
            # Wait before next update
            import time
            time.sleep(5)
            
    except KeyboardInterrupt:
        console.print("\n[yellow]Monitor stopped[/yellow]")
    
    asyncio.run(client.close())


# Additional utility commands
@cli.group()
def config():
    """Configuration management"""
    pass


@config.command(name='show')
def config_show():
    """Show current configuration"""
    config_data = {
        'api_url': API_URL,
        'log_level': os.getenv('LOG_LEVEL', 'INFO'),
        'config_file': os.getenv('CONFIG_FILE', 'config.yaml')
    }
    
    console.print(json.dumps(config_data, indent=2))


@config.command(name='set')
@click.argument('key')
@click.argument('value')
def config_set(key, value):
    """Set configuration value"""
    console.print(f"[yellow]Setting {key}={value}[/yellow]")
    console.print("[dim]Note: This only updates environment for current session[/dim]")
    os.environ[key] = value


@cli.group()
def logs():
    """Log management"""
    pass


@logs.command(name='tail')
@click.option('--lines', '-n', default=50, help='Number of lines to show')
@click.option('--follow', '-f', is_flag=True, help='Follow log output')
def logs_tail(lines, follow):
    """Show recent logs"""
    log_file = os.path.join('logs', 'main.log')
    
    if not os.path.exists(log_file):
        console.print(f"[red]Log file not found: {log_file}[/red]")
        return
    
    try:
        with open(log_file, 'r') as f:
            all_lines = f.readlines()
            recent_lines = all_lines[-lines:]
            
            for line in recent_lines:
                try:
                    entry = json.loads(line)
                    level = entry.get('level', 'INFO')
                    color = {
                        'DEBUG': 'dim',
                        'INFO': 'white',
                        'WARNING': 'yellow',
                        'ERROR': 'red',
                        'CRITICAL': 'red bold'
                    }.get(level, 'white')
                    
                    timestamp = entry.get('timestamp', 'N/A')[:19]
                    message = entry.get('message', '')
                    
                    console.print(f"[{color}]{timestamp} | {level:8} | {message}[/{color}]")
                except json.JSONDecodeError:
                    console.print(line.strip())
    
    except Exception as e:
        console.print(f"[red]Error reading logs: {e}[/red]")


# Entry point
def main():
    """CLI entry point"""
    try:
        cli()
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        sys.exit(1)


if __name__ == '__main__':
    main()
