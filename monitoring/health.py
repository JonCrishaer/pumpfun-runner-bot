"""
Health Check System for Pump.fun Trading Bot
Monitors API connections, wallet balance, system resources, and signal freshness
"""

import asyncio
import aiohttp
import psutil
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Callable, Any
from dataclasses import dataclass, asdict, field
from enum import Enum
import json
import threading
from collections import deque
import logging


class HealthStatus(Enum):
    """Health check status levels"""
    HEALTHY = "healthy"
    WARNING = "warning"
    CRITICAL = "critical"
    UNKNOWN = "unknown"


@dataclass
class HealthCheckResult:
    """Result of a health check"""
    name: str
    status: HealthStatus
    message: str
    timestamp: datetime
    response_time_ms: float
    details: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'name': self.name,
            'status': self.status.value,
            'message': self.message,
            'timestamp': self.timestamp.isoformat(),
            'response_time_ms': self.response_time_ms,
            'details': self.details
        }


@dataclass
class SystemMetrics:
    """System resource metrics"""
    cpu_percent: float
    memory_percent: float
    memory_used_mb: float
    memory_total_mb: float
    disk_percent: float
    network_io_mb: float
    process_count: int
    timestamp: datetime
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'cpu_percent': self.cpu_percent,
            'memory_percent': self.memory_percent,
            'memory_used_mb': self.memory_used_mb,
            'memory_total_mb': self.memory_total_mb,
            'disk_percent': self.disk_percent,
            'network_io_mb': self.network_io_mb,
            'process_count': self.process_count,
            'timestamp': self.timestamp.isoformat()
        }


class HealthCheck(ABC):
    """Abstract base class for health checks"""
    
    def __init__(self, name: str, interval: int = 60):
        self.name = name
        self.interval = interval  # seconds
        self.last_check: Optional[datetime] = None
        self.last_result: Optional[HealthCheckResult] = None
    
    @abstractmethod
    async def check(self) -> HealthCheckResult:
        """Perform health check"""
        pass
    
    async def run(self) -> HealthCheckResult:
        """Run check and store result"""
        start_time = time.time()
        try:
            result = await self.check()
        except Exception as e:
            result = HealthCheckResult(
                name=self.name,
                status=HealthStatus.CRITICAL,
                message=f"Check failed: {str(e)}",
                timestamp=datetime.now(),
                response_time_ms=(time.time() - start_time) * 1000,
                details={'error': str(e)}
            )
        
        self.last_check = datetime.now()
        self.last_result = result
        return result


class RPCHealthCheck(HealthCheck):
    """Check RPC node connection health"""
    
    def __init__(self, rpc_url: str, timeout: int = 10):
        super().__init__("rpc_node", interval=30)
        self.rpc_url = rpc_url
        self.timeout = timeout
    
    async def check(self) -> HealthCheckResult:
        start_time = time.time()
        
        try:
            async with aiohttp.ClientSession() as session:
                payload = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "getHealth"
                }
                
                async with session.post(
                    self.rpc_url,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=self.timeout)
                ) as response:
                    response_time = (time.time() - start_time) * 1000
                    
                    if response.status == 200:
                        data = await response.json()
                        
                        if 'error' in data:
                            return HealthCheckResult(
                                name=self.name,
                                status=HealthStatus.CRITICAL,
                                message=f"RPC error: {data['error']}",
                                timestamp=datetime.now(),
                                response_time_ms=response_time,
                                details={'rpc_response': data}
                            )
                        
                        return HealthCheckResult(
                            name=self.name,
                            status=HealthStatus.HEALTHY,
                            message="RPC node is healthy",
                            timestamp=datetime.now(),
                            response_time_ms=response_time,
                            details={'slot': data.get('result', {}).get('slot', 0)}
                        )
                    else:
                        return HealthCheckResult(
                            name=self.name,
                            status=HealthStatus.CRITICAL,
                            message=f"RPC returned status {response.status}",
                            timestamp=datetime.now(),
                            response_time_ms=response_time,
                            details={'status_code': response.status}
                        )
                        
        except asyncio.TimeoutError:
            return HealthCheckResult(
                name=self.name,
                status=HealthStatus.CRITICAL,
                message=f"RPC timeout after {self.timeout}s",
                timestamp=datetime.now(),
                response_time_ms=(time.time() - start_time) * 1000,
                details={'timeout': self.timeout}
            )
        except Exception as e:
            return HealthCheckResult(
                name=self.name,
                status=HealthStatus.CRITICAL,
                message=f"RPC connection failed: {str(e)}",
                timestamp=datetime.now(),
                response_time_ms=(time.time() - start_time) * 1000,
                details={'error': str(e)}
            )


class WebSocketHealthCheck(HealthCheck):
    """Check WebSocket connection health"""
    
    def __init__(self, ws_url: str, timeout: int = 10):
        super().__init__("websocket", interval=30)
        self.ws_url = ws_url
        self.timeout = timeout
    
    async def check(self) -> HealthCheckResult:
        start_time = time.time()
        
        try:
            import websockets
            
            async with websockets.connect(
                self.ws_url,
                timeout=self.timeout
            ) as websocket:
                response_time = (time.time() - start_time) * 1000
                
                # Try to send a ping
                pong = await websocket.ping()
                await asyncio.wait_for(pong, timeout=5)
                
                return HealthCheckResult(
                    name=self.name,
                    status=HealthStatus.HEALTHY,
                    message="WebSocket connection is healthy",
                    timestamp=datetime.now(),
                    response_time_ms=response_time,
                    details={'ping_ms': response_time}
                )
                
        except ImportError:
            return HealthCheckResult(
                name=self.name,
                status=HealthStatus.WARNING,
                message="WebSocket library not installed",
                timestamp=datetime.now(),
                response_time_ms=(time.time() - start_time) * 1000,
                details={}
            )
        except Exception as e:
            return HealthCheckResult(
                name=self.name,
                status=HealthStatus.CRITICAL,
                message=f"WebSocket connection failed: {str(e)}",
                timestamp=datetime.now(),
                response_time_ms=(time.time() - start_time) * 1000,
                details={'error': str(e)}
            )


class WalletBalanceCheck(HealthCheck):
    """Check wallet balance"""
    
    def __init__(self, rpc_url: str, wallet_address: str, 
                 min_balance_sol: float = 0.1):
        super().__init__("wallet_balance", interval=60)
        self.rpc_url = rpc_url
        self.wallet_address = wallet_address
        self.min_balance_sol = min_balance_sol
    
    async def check(self) -> HealthCheckResult:
        start_time = time.time()
        
        try:
            async with aiohttp.ClientSession() as session:
                payload = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "getBalance",
                    "params": [self.wallet_address]
                }
                
                async with session.post(
                    self.rpc_url,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as response:
                    response_time = (time.time() - start_time) * 1000
                    
                    if response.status == 200:
                        data = await response.json()
                        
                        if 'error' in data:
                            return HealthCheckResult(
                                name=self.name,
                                status=HealthStatus.CRITICAL,
                                message=f"Balance check failed: {data['error']}",
                                timestamp=datetime.now(),
                                response_time_ms=response_time,
                                details={}
                            )
                        
                        # Convert lamports to SOL
                        balance_lamports = data['result']['value']
                        balance_sol = balance_lamports / 1e9
                        
                        if balance_sol < self.min_balance_sol:
                            return HealthCheckResult(
                                name=self.name,
                                status=HealthStatus.CRITICAL,
                                message=f"Low balance: {balance_sol:.4f} SOL",
                                timestamp=datetime.now(),
                                response_time_ms=response_time,
                                details={
                                    'balance_sol': balance_sol,
                                    'balance_lamports': balance_lamports,
                                    'min_required': self.min_balance_sol
                                }
                            )
                        
                        return HealthCheckResult(
                            name=self.name,
                            status=HealthStatus.HEALTHY,
                            message=f"Balance: {balance_sol:.4f} SOL",
                            timestamp=datetime.now(),
                            response_time_ms=response_time,
                            details={
                                'balance_sol': balance_sol,
                                'balance_lamports': balance_lamports
                            }
                        )
                    else:
                        return HealthCheckResult(
                            name=self.name,
                            status=HealthStatus.CRITICAL,
                            message=f"Balance check returned {response.status}",
                            timestamp=datetime.now(),
                            response_time_ms=response_time,
                            details={'status_code': response.status}
                        )
                        
        except Exception as e:
            return HealthCheckResult(
                name=self.name,
                status=HealthStatus.CRITICAL,
                message=f"Balance check failed: {str(e)}",
                timestamp=datetime.now(),
                response_time_ms=(time.time() - start_time) * 1000,
                details={'error': str(e)}
            )


class SignalFreshnessCheck(HealthCheck):
    """Check if signals are being generated"""
    
    def __init__(self, max_age_seconds: int = 300):
        super().__init__("signal_freshness", interval=60)
        self.max_age_seconds = max_age_seconds
        self.last_signal_time: Optional[datetime] = None
    
    def update_last_signal(self):
        """Update timestamp of last signal"""
        self.last_signal_time = datetime.now()
    
    async def check(self) -> HealthCheckResult:
        start_time = time.time()
        
        if self.last_signal_time is None:
            return HealthCheckResult(
                name=self.name,
                status=HealthStatus.WARNING,
                message="No signals generated yet",
                timestamp=datetime.now(),
                response_time_ms=(time.time() - start_time) * 1000,
                details={'last_signal': None}
            )
        
        age_seconds = (datetime.now() - self.last_signal_time).total_seconds()
        
        if age_seconds > self.max_age_seconds:
            return HealthCheckResult(
                name=self.name,
                status=HealthStatus.CRITICAL,
                message=f"Last signal was {age_seconds:.0f}s ago",
                timestamp=datetime.now(),
                response_time_ms=(time.time() - start_time) * 1000,
                details={
                    'last_signal': self.last_signal_time.isoformat(),
                    'age_seconds': age_seconds,
                    'max_age': self.max_age_seconds
                }
            )
        
        return HealthCheckResult(
            name=self.name,
            status=HealthStatus.HEALTHY,
            message=f"Last signal was {age_seconds:.0f}s ago",
            timestamp=datetime.now(),
            response_time_ms=(time.time() - start_time) * 1000,
            details={
                'last_signal': self.last_signal_time.isoformat(),
                'age_seconds': age_seconds
            }
        )


class SystemResourcesCheck(HealthCheck):
    """Check system resource usage"""
    
    def __init__(self, max_cpu_percent: float = 80,
                 max_memory_percent: float = 85,
                 max_disk_percent: float = 90):
        super().__init__("system_resources", interval=30)
        self.max_cpu_percent = max_cpu_percent
        self.max_memory_percent = max_memory_percent
        self.max_disk_percent = max_disk_percent
    
    async def check(self) -> HealthCheckResult:
        start_time = time.time()
        
        try:
            # Get CPU usage
            cpu_percent = psutil.cpu_percent(interval=1)
            
            # Get memory usage
            memory = psutil.virtual_memory()
            
            # Get disk usage
            disk = psutil.disk_usage('/')
            
            # Get network I/O
            net_io = psutil.net_io_counters()
            
            # Get process count
            process_count = len(psutil.pids())
            
            metrics = SystemMetrics(
                cpu_percent=cpu_percent,
                memory_percent=memory.percent,
                memory_used_mb=memory.used / 1024 / 1024,
                memory_total_mb=memory.total / 1024 / 1024,
                disk_percent=(disk.used / disk.total) * 100,
                network_io_mb=(net_io.bytes_sent + net_io.bytes_recv) / 1024 / 1024,
                process_count=process_count,
                timestamp=datetime.now()
            )
            
            response_time = (time.time() - start_time) * 1000
            
            # Determine status
            if cpu_percent > self.max_cpu_percent:
                return HealthCheckResult(
                    name=self.name,
                    status=HealthStatus.WARNING,
                    message=f"High CPU usage: {cpu_percent:.1f}%",
                    timestamp=datetime.now(),
                    response_time_ms=response_time,
                    details=metrics.to_dict()
                )
            
            if memory.percent > self.max_memory_percent:
                return HealthCheckResult(
                    name=self.name,
                    status=HealthStatus.WARNING,
                    message=f"High memory usage: {memory.percent:.1f}%",
                    timestamp=datetime.now(),
                    response_time_ms=response_time,
                    details=metrics.to_dict()
                )
            
            if (disk.used / disk.total) * 100 > self.max_disk_percent:
                return HealthCheckResult(
                    name=self.name,
                    status=HealthStatus.WARNING,
                    message=f"High disk usage: {(disk.used / disk.total) * 100:.1f}%",
                    timestamp=datetime.now(),
                    response_time_ms=response_time,
                    details=metrics.to_dict()
                )
            
            return HealthCheckResult(
                name=self.name,
                status=HealthStatus.HEALTHY,
                message=f"System resources healthy (CPU: {cpu_percent:.1f}%, Memory: {memory.percent:.1f}%)",
                timestamp=datetime.now(),
                response_time_ms=response_time,
                details=metrics.to_dict()
            )
            
        except Exception as e:
            return HealthCheckResult(
                name=self.name,
                status=HealthStatus.CRITICAL,
                message=f"Resource check failed: {str(e)}",
                timestamp=datetime.now(),
                response_time_ms=(time.time() - start_time) * 1000,
                details={'error': str(e)}
            )


class APIEndpointCheck(HealthCheck):
    """Check external API endpoint health"""
    
    def __init__(self, name: str, url: str, timeout: int = 10,
                 expected_status: int = 200):
        super().__init__(name, interval=60)
        self.url = url
        self.timeout = timeout
        self.expected_status = expected_status
    
    async def check(self) -> HealthCheckResult:
        start_time = time.time()
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    self.url,
                    timeout=aiohttp.ClientTimeout(total=self.timeout)
                ) as response:
                    response_time = (time.time() - start_time) * 1000
                    
                    if response.status == self.expected_status:
                        return HealthCheckResult(
                            name=self.name,
                            status=HealthStatus.HEALTHY,
                            message=f"API is healthy (status {response.status})",
                            timestamp=datetime.now(),
                            response_time_ms=response_time,
                            details={'status_code': response.status}
                        )
                    else:
                        return HealthCheckResult(
                            name=self.name,
                            status=HealthStatus.WARNING,
                            message=f"API returned unexpected status {response.status}",
                            timestamp=datetime.now(),
                            response_time_ms=response_time,
                            details={'status_code': response.status, 'expected': self.expected_status}
                        )
                        
        except asyncio.TimeoutError:
            return HealthCheckResult(
                name=self.name,
                status=HealthStatus.CRITICAL,
                message=f"API timeout after {self.timeout}s",
                timestamp=datetime.now(),
                response_time_ms=(time.time() - start_time) * 1000,
                details={'timeout': self.timeout}
            )
        except Exception as e:
            return HealthCheckResult(
                name=self.name,
                status=HealthStatus.CRITICAL,
                message=f"API check failed: {str(e)}",
                timestamp=datetime.now(),
                response_time_ms=(time.time() - start_time) * 1000,
                details={'error': str(e)}
            )


class HealthMonitor:
    """Main health monitoring system"""
    
    def __init__(self, alert_callback: Optional[Callable] = None):
        self.checks: Dict[str, HealthCheck] = {}
        self.results_history: deque = deque(maxlen=1000)
        self.alert_callback = alert_callback
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._lock = threading.Lock()
    
    def add_check(self, check: HealthCheck):
        """Add a health check"""
        self.checks[check.name] = check
    
    def remove_check(self, name: str):
        """Remove a health check"""
        if name in self.checks:
            del self.checks[name]
    
    async def run_single_check(self, name: str) -> Optional[HealthCheckResult]:
        """Run a single health check"""
        if name not in self.checks:
            return None
        
        check = self.checks[name]
        result = await check.run()
        
        with self._lock:
            self.results_history.append(result)
        
        # Trigger alert if critical
        if result.status == HealthStatus.CRITICAL and self.alert_callback:
            await self.alert_callback(result)
        
        return result
    
    async def run_all_checks(self) -> Dict[str, HealthCheckResult]:
        """Run all health checks"""
        results = {}
        
        for name, check in self.checks.items():
            result = await self.run_single_check(name)
            if result:
                results[name] = result
        
        return results
    
    async def _monitor_loop(self):
        """Main monitoring loop"""
        while self._running:
            for name, check in self.checks.items():
                # Check if it's time to run this check
                if (check.last_check is None or 
                    (datetime.now() - check.last_check).total_seconds() >= check.interval):
                    await self.run_single_check(name)
            
            await asyncio.sleep(5)  # Check every 5 seconds
    
    def start(self):
        """Start the health monitor"""
        if not self._running:
            self._running = True
            self._task = asyncio.create_task(self._monitor_loop())
    
    def stop(self):
        """Stop the health monitor"""
        self._running = False
        if self._task:
            self._task.cancel()
    
    def get_status(self) -> Dict[str, Any]:
        """Get overall health status"""
        if not self.checks:
            return {
                'status': HealthStatus.UNKNOWN.value,
                'checks': {},
                'summary': {'healthy': 0, 'warning': 0, 'critical': 0, 'unknown': 0}
            }
        
        summary = {'healthy': 0, 'warning': 0, 'critical': 0, 'unknown': 0}
        checks_status = {}
        
        for name, check in self.checks.items():
            if check.last_result:
                checks_status[name] = check.last_result.to_dict()
                summary[check.last_result.status.value] += 1
            else:
                summary['unknown'] += 1
        
        # Determine overall status
        if summary['critical'] > 0:
            overall = HealthStatus.CRITICAL
        elif summary['warning'] > 0:
            overall = HealthStatus.WARNING
        elif summary['healthy'] > 0:
            overall = HealthStatus.HEALTHY
        else:
            overall = HealthStatus.UNKNOWN
        
        return {
            'status': overall.value,
            'checks': checks_status,
            'summary': summary,
            'timestamp': datetime.now().isoformat()
        }
    
    def get_check_history(self, name: str, limit: int = 100) -> List[Dict]:
        """Get history for a specific check"""
        with self._lock:
            history = [r for r in self.results_history if r.name == name]
            return [r.to_dict() for r in history[-limit:]]


# Example usage
async def main():
    """Test health monitoring"""
    
    async def alert_handler(result: HealthCheckResult):
        print(f"ALERT: {result.message}")
    
    monitor = HealthMonitor(alert_callback=alert_handler)
    
    # Add checks
    monitor.add_check(SystemResourcesCheck())
    monitor.add_check(SignalFreshnessCheck(max_age_seconds=60))
    
    # Run checks once
    results = await monitor.run_all_checks()
    
    for name, result in results.items():
        print(f"{name}: {result.status.value} - {result.message}")
    
    # Get status
    status = monitor.get_status()
    print(f"\nOverall status: {status['status']}")
    print(f"Summary: {status['summary']}")


if __name__ == "__main__":
    asyncio.run(main())
