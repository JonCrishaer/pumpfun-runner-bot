"""
Alert System for Pump.fun Trading Bot
Supports Telegram, Discord, Email, and SMS notifications
"""

import asyncio
import aiohttp
import json
import smtplib
import ssl
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional, Callable, Any
from dataclasses import dataclass, asdict
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import logging
from abc import ABC, abstractmethod
import threading
import queue
from twilio.rest import Client as TwilioClient


class AlertLevel(Enum):
    """Alert severity levels"""
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class AlertCategory(Enum):
    """Alert categories"""
    SIGNAL = "signal"
    EXECUTION = "execution"
    POSITION = "position"
    SYSTEM = "system"
    SECURITY = "security"
    PERFORMANCE = "performance"


@dataclass
class Alert:
    """Alert data structure"""
    id: str
    timestamp: datetime
    level: AlertLevel
    category: AlertCategory
    message: str
    details: Dict[str, Any]
    confidence: float = 1.0
    factors: List[str] = None
    token_symbol: Optional[str] = None
    
    def __post_init__(self):
        if self.factors is None:
            self.factors = []
    
    def to_dict(self) -> Dict:
        return {
            'id': self.id,
            'timestamp': self.timestamp.isoformat(),
            'level': self.level.value,
            'category': self.category.value,
            'message': self.message,
            'details': self.details,
            'confidence': self.confidence,
            'factors': self.factors,
            'token_symbol': self.token_symbol
        }


class AlertChannel(ABC):
    """Abstract base class for alert channels"""
    
    @abstractmethod
    async def send(self, alert: Alert) -> bool:
        """Send alert through this channel"""
        pass
    
    @abstractmethod
    def is_configured(self) -> bool:
        """Check if channel is properly configured"""
        pass


class TelegramChannel(AlertChannel):
    """Telegram bot alert channel"""
    
    def __init__(self, bot_token: str, chat_ids: List[str]):
        self.bot_token = bot_token
        self.chat_ids = chat_ids
        self.base_url = f"https://api.telegram.org/bot{bot_token}"
        self.session: Optional[aiohttp.ClientSession] = None
    
    def is_configured(self) -> bool:
        return bool(self.bot_token and self.chat_ids)
    
    async def _get_session(self) -> aiohttp.ClientSession:
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession()
        return self.session
    
    def _format_message(self, alert: Alert) -> str:
        """Format alert for Telegram"""
        level_emoji = {
            AlertLevel.DEBUG: "🔍",
            AlertLevel.INFO: "ℹ️",
            AlertLevel.WARNING: "⚠️",
            AlertLevel.ERROR: "❌",
            AlertLevel.CRITICAL: "🚨"
        }
        
        category_emoji = {
            AlertCategory.SIGNAL: "📊",
            AlertCategory.EXECUTION: "💼",
            AlertCategory.POSITION: "📈",
            AlertCategory.SYSTEM: "⚙️",
            AlertCategory.SECURITY: "🔒",
            AlertCategory.PERFORMANCE: "🚀"
        }
        
        emoji = level_emoji.get(alert.level, "📢")
        cat_emoji = category_emoji.get(alert.category, "📋")
        
        message = f"""
{emoji} <b>{alert.level.value.upper()} ALERT</b> {cat_emoji}

<b>Message:</b> {alert.message}

<b>Category:</b> {alert.category.value}
<b>Time:</b> {alert.timestamp.strftime('%Y-%m-%d %H:%M:%S')}
<b>Confidence:</b> {alert.confidence:.0%}
"""
        
        if alert.token_symbol:
            message += f"<b>Token:</b> {alert.token_symbol}\n"
        
        if alert.factors:
            message += f"\n<b>Factors:</b>\n"
            for factor in alert.factors:
                message += f"  • {factor}\n"
        
        if alert.details:
            message += f"\n<b>Details:</b>\n"
            for key, value in alert.details.items():
                message += f"  {key}: {value}\n"
        
        return message
    
    async def send(self, alert: Alert) -> bool:
        if not self.is_configured():
            return False
        
        try:
            session = await self._get_session()
            message = self._format_message(alert)
            
            for chat_id in self.chat_ids:
                url = f"{self.base_url}/sendMessage"
                payload = {
                    'chat_id': chat_id,
                    'text': message,
                    'parse_mode': 'HTML'
                }
                
                async with session.post(url, json=payload) as response:
                    if response.status != 200:
                        logging.error(f"Telegram send failed: {await response.text()}")
                        return False
            
            return True
            
        except Exception as e:
            logging.error(f"Telegram channel error: {e}")
            return False
    
    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()


class DiscordChannel(AlertChannel):
    """Discord webhook alert channel"""
    
    def __init__(self, webhook_urls: List[str]):
        self.webhook_urls = webhook_urls
        self.session: Optional[aiohttp.ClientSession] = None
    
    def is_configured(self) -> bool:
        return bool(self.webhook_urls)
    
    async def _get_session(self) -> aiohttp.ClientSession:
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession()
        return self.session
    
    def _format_embed(self, alert: Alert) -> Dict:
        """Format alert as Discord embed"""
        color_map = {
            AlertLevel.DEBUG: 0x808080,
            AlertLevel.INFO: 0x00aaff,
            AlertLevel.WARNING: 0xffaa00,
            AlertLevel.ERROR: 0xff4444,
            AlertLevel.CRITICAL: 0xff0000
        }
        
        embed = {
            'title': f"{alert.level.value.upper()} Alert",
            'description': alert.message,
            'color': color_map.get(alert.level, 0x808080),
            'timestamp': alert.timestamp.isoformat(),
            'fields': []
        }
        
        embed['fields'].append({
            'name': 'Category',
            'value': alert.category.value,
            'inline': True
        })
        
        embed['fields'].append({
            'name': 'Confidence',
            'value': f"{alert.confidence:.0%}",
            'inline': True
        })
        
        if alert.token_symbol:
            embed['fields'].append({
                'name': 'Token',
                'value': alert.token_symbol,
                'inline': True
            })
        
        if alert.factors:
            embed['fields'].append({
                'name': 'Factors',
                'value': '\n'.join(f"• {f}" for f in alert.factors),
                'inline': False
            })
        
        if alert.details:
            details_text = '\n'.join(f"{k}: {v}" for k, v in alert.details.items())
            embed['fields'].append({
                'name': 'Details',
                'value': details_text[:1024],  # Discord field limit
                'inline': False
            })
        
        return embed
    
    async def send(self, alert: Alert) -> bool:
        if not self.is_configured():
            return False
        
        try:
            session = await self._get_session()
            embed = self._format_embed(alert)
            
            for webhook_url in self.webhook_urls:
                payload = {'embeds': [embed]}
                
                async with session.post(webhook_url, json=payload) as response:
                    if response.status not in [200, 204]:
                        logging.error(f"Discord send failed: {await response.text()}")
                        return False
            
            return True
            
        except Exception as e:
            logging.error(f"Discord channel error: {e}")
            return False
    
    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()


class EmailChannel(AlertChannel):
    """Email alert channel"""
    
    def __init__(self, smtp_host: str, smtp_port: int, username: str, 
                 password: str, from_addr: str, to_addrs: List[str],
                 use_tls: bool = True):
        self.smtp_host = smtp_host
        self.smtp_port = smtp_port
        self.username = username
        self.password = password
        self.from_addr = from_addr
        self.to_addrs = to_addrs
        self.use_tls = use_tls
    
    def is_configured(self) -> bool:
        return all([self.smtp_host, self.username, self.password, self.to_addrs])
    
    def _format_message(self, alert: Alert) -> MIMEMultipart:
        """Format alert as email"""
        msg = MIMEMultipart('alternative')
        msg['Subject'] = f"[{alert.level.value.upper()}] Pump.fun Trader Alert"
        msg['From'] = self.from_addr
        msg['To'] = ', '.join(self.to_addrs)
        
        # Plain text version
        text_body = f"""
Alert Level: {alert.level.value.upper()}
Category: {alert.category.value}
Time: {alert.timestamp.strftime('%Y-%m-%d %H:%M:%S')}
Message: {alert.message}
Confidence: {alert.confidence:.0%}
"""
        
        if alert.token_symbol:
            text_body += f"Token: {alert.token_symbol}\n"
        
        if alert.factors:
            text_body += f"\nFactors:\n" + '\n'.join(f"  - {f}" for f in alert.factors)
        
        if alert.details:
            text_body += f"\nDetails:\n" + '\n'.join(f"  {k}: {v}" for k, v in alert.details.items())
        
        # HTML version
        color_map = {
            AlertLevel.DEBUG: '#808080',
            AlertLevel.INFO: '#00aaff',
            AlertLevel.WARNING: '#ffaa00',
            AlertLevel.ERROR: '#ff4444',
            AlertLevel.CRITICAL: '#ff0000'
        }
        
        html_body = f"""
        <html>
        <body style="font-family: Arial, sans-serif;">
            <div style="border: 2px solid {color_map.get(alert.level, '#808080')}; padding: 20px; border-radius: 10px;">
                <h2 style="color: {color_map.get(alert.level, '#808080')};">
                    {alert.level.value.upper()} Alert
                </h2>
                <p><strong>Message:</strong> {alert.message}</p>
                <p><strong>Category:</strong> {alert.category.value}</p>
                <p><strong>Time:</strong> {alert.timestamp.strftime('%Y-%m-%d %H:%M:%S')}</p>
                <p><strong>Confidence:</strong> {alert.confidence:.0%}</p>
        """
        
        if alert.token_symbol:
            html_body += f"<p><strong>Token:</strong> {alert.token_symbol}</p>"
        
        if alert.factors:
            html_body += "<p><strong>Factors:</strong></p><ul>"
            for factor in alert.factors:
                html_body += f"<li>{factor}</li>"
            html_body += "</ul>"
        
        if alert.details:
            html_body += "<p><strong>Details:</strong></p><ul>"
            for key, value in alert.details.items():
                html_body += f"<li>{key}: {value}</li>"
            html_body += "</ul>"
        
        html_body += "</div></body></html>"
        
        msg.attach(MIMEText(text_body, 'plain'))
        msg.attach(MIMEText(html_body, 'html'))
        
        return msg
    
    async def send(self, alert: Alert) -> bool:
        if not self.is_configured():
            return False
        
        try:
            msg = self._format_message(alert)
            
            # Run SMTP in thread pool to avoid blocking
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self._send_smtp, msg)
            
            return True
            
        except Exception as e:
            logging.error(f"Email channel error: {e}")
            return False
    
    def _send_smtp(self, msg: MIMEMultipart):
        """Send email via SMTP"""
        context = ssl.create_default_context()
        
        with smtplib.SMTP(self.smtp_host, self.smtp_port) as server:
            if self.use_tls:
                server.starttls(context=context)
            server.login(self.username, self.password)
            server.send_message(msg)


class SMSChannel(AlertChannel):
    """SMS alert channel using Twilio"""
    
    def __init__(self, account_sid: str, auth_token: str, 
                 from_number: str, to_numbers: List[str]):
        self.account_sid = account_sid
        self.auth_token = auth_token
        self.from_number = from_number
        self.to_numbers = to_numbers
        self.client: Optional[TwilioClient] = None
    
    def is_configured(self) -> bool:
        return all([self.account_sid, self.auth_token, self.from_number, self.to_numbers])
    
    def _get_client(self) -> TwilioClient:
        if self.client is None:
            self.client = TwilioClient(self.account_sid, self.auth_token)
        return self.client
    
    def _format_message(self, alert: Alert) -> str:
        """Format alert for SMS (short format)"""
        return f"[{alert.level.value.upper()}] {alert.message[:100]}"
    
    async def send(self, alert: Alert) -> bool:
        if not self.is_configured():
            return False
        
        # Only send CRITICAL and ERROR alerts via SMS
        if alert.level not in [AlertLevel.CRITICAL, AlertLevel.ERROR]:
            return True
        
        try:
            client = self._get_client()
            message_body = self._format_message(alert)
            
            # Run Twilio in thread pool
            loop = asyncio.get_event_loop()
            
            for to_number in self.to_numbers:
                await loop.run_in_executor(
                    None,
                    lambda: client.messages.create(
                        body=message_body,
                        from_=self.from_number,
                        to=to_number
                    )
                )
            
            return True
            
        except Exception as e:
            logging.error(f"SMS channel error: {e}")
            return False


class AlertManager:
    """Main alert manager coordinating all channels"""
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.channels: Dict[str, AlertChannel] = {}
        self.alert_history: List[Alert] = []
        self.max_history = 1000
        self._lock = threading.Lock()
        
        # Initialize channels based on config
        self._init_channels()
        
        # Rate limiting
        self.rate_limits: Dict[str, datetime] = {}
        self.min_interval = config.get('min_alert_interval', 5)  # seconds
    
    def _init_channels(self):
        """Initialize alert channels from config"""
        # Telegram
        if 'telegram' in self.config:
            tg_config = self.config['telegram']
            self.channels['telegram'] = TelegramChannel(
                bot_token=tg_config.get('bot_token', ''),
                chat_ids=tg_config.get('chat_ids', [])
            )
        
        # Discord
        if 'discord' in self.config:
            dc_config = self.config['discord']
            self.channels['discord'] = DiscordChannel(
                webhook_urls=dc_config.get('webhook_urls', [])
            )
        
        # Email
        if 'email' in self.config:
            email_config = self.config['email']
            self.channels['email'] = EmailChannel(
                smtp_host=email_config.get('smtp_host', ''),
                smtp_port=email_config.get('smtp_port', 587),
                username=email_config.get('username', ''),
                password=email_config.get('password', ''),
                from_addr=email_config.get('from_addr', ''),
                to_addrs=email_config.get('to_addrs', []),
                use_tls=email_config.get('use_tls', True)
            )
        
        # SMS
        if 'sms' in self.config:
            sms_config = self.config['sms']
            self.channels['sms'] = SMSChannel(
                account_sid=sms_config.get('account_sid', ''),
                auth_token=sms_config.get('auth_token', ''),
                from_number=sms_config.get('from_number', ''),
                to_numbers=sms_config.get('to_numbers', [])
            )
    
    def _check_rate_limit(self, alert_key: str) -> bool:
        """Check if alert is within rate limit"""
        now = datetime.now()
        if alert_key in self.rate_limits:
            elapsed = (now - self.rate_limits[alert_key]).total_seconds()
            if elapsed < self.min_interval:
                return False
        self.rate_limits[alert_key] = now
        return True
    
    async def send_alert(self, level: AlertLevel, category: AlertCategory,
                        message: str, details: Dict[str, Any] = None,
                        confidence: float = 1.0, factors: List[str] = None,
                        token_symbol: Optional[str] = None,
                        channels: List[str] = None) -> Dict[str, bool]:
        """
        Send alert through configured channels
        
        Args:
            level: Alert severity level
            category: Alert category
            message: Alert message
            details: Additional details
            confidence: Confidence score (0-1)
            factors: List of trigger factors
            token_symbol: Related token symbol
            channels: Specific channels to use (None = all)
        
        Returns:
            Dict of channel names to success status
        """
        if details is None:
            details = {}
        
        # Create alert
        alert = Alert(
            id=f"{datetime.now().timestamp()}",
            timestamp=datetime.now(),
            level=level,
            category=category,
            message=message,
            details=details,
            confidence=confidence,
            factors=factors or [],
            token_symbol=token_symbol
        )
        
        # Add to history
        with self._lock:
            self.alert_history.append(alert)
            if len(self.alert_history) > self.max_history:
                self.alert_history = self.alert_history[-self.max_history:]
        
        # Check rate limit
        alert_key = f"{category.value}:{message}"
        if not self._check_rate_limit(alert_key):
            logging.debug(f"Alert rate limited: {alert_key}")
            return {}
        
        # Determine which channels to use
        target_channels = channels or list(self.channels.keys())
        
        # Filter by alert level for certain channels
        if level == AlertLevel.CRITICAL:
            # Critical alerts go to all channels
            pass
        elif level == AlertLevel.ERROR:
            # Errors don't go to SMS unless configured
            if 'sms' in target_channels and not self.config.get('sms_errors', False):
                target_channels.remove('sms')
        elif level in [AlertLevel.WARNING, AlertLevel.INFO]:
            # Warnings and info only go to Telegram/Discord
            target_channels = [c for c in target_channels if c in ['telegram', 'discord']]
        else:
            # Debug only goes to configured channels
            target_channels = [c for c in target_channels if self.config.get(f'debug_{c}', False)]
        
        # Send to channels
        results = {}
        for channel_name in target_channels:
            if channel_name in self.channels:
                channel = self.channels[channel_name]
                if channel.is_configured():
                    results[channel_name] = await channel.send(alert)
                else:
                    results[channel_name] = False
        
        return results
    
    # Convenience methods
    async def debug(self, message: str, **kwargs):
        return await self.send_alert(AlertLevel.DEBUG, AlertCategory.SYSTEM, message, **kwargs)
    
    async def info(self, message: str, **kwargs):
        return await self.send_alert(AlertLevel.INFO, AlertCategory.SYSTEM, message, **kwargs)
    
    async def warning(self, message: str, **kwargs):
        return await self.send_alert(AlertLevel.WARNING, AlertCategory.SYSTEM, message, **kwargs)
    
    async def error(self, message: str, **kwargs):
        return await self.send_alert(AlertLevel.ERROR, AlertCategory.SYSTEM, message, **kwargs)
    
    async def critical(self, message: str, **kwargs):
        return await self.send_alert(AlertLevel.CRITICAL, AlertCategory.SYSTEM, message, **kwargs)
    
    async def signal(self, message: str, token_symbol: str, confidence: float,
                    factors: List[str], **kwargs):
        """Send trading signal alert"""
        return await self.send_alert(
            AlertLevel.INFO,
            AlertCategory.SIGNAL,
            message,
            token_symbol=token_symbol,
            confidence=confidence,
            factors=factors,
            **kwargs
        )
    
    async def position_update(self, message: str, token_symbol: str,
                             pnl_pct: float, **kwargs):
        """Send position update alert"""
        level = AlertLevel.INFO if pnl_pct > 0 else AlertLevel.WARNING
        return await self.send_alert(
            level,
            AlertCategory.POSITION,
            message,
            token_symbol=token_symbol,
            details={'pnl_pct': f"{pnl_pct:+.2f}%"},
            **kwargs
        )
    
    def get_alert_history(self, level: AlertLevel = None, 
                         category: AlertCategory = None,
                         limit: int = 100) -> List[Alert]:
        """Get alert history with optional filtering"""
        alerts = self.alert_history
        
        if level:
            alerts = [a for a in alerts if a.level == level]
        
        if category:
            alerts = [a for a in alerts if a.category == category]
        
        return alerts[-limit:]
    
    async def close(self):
        """Close all channels"""
        for channel in self.channels.values():
            if hasattr(channel, 'close'):
                await channel.close()


# Example usage and testing
async def main():
    """Test alert system"""
    config = {
        'telegram': {
            'bot_token': 'YOUR_BOT_TOKEN',
            'chat_ids': ['YOUR_CHAT_ID']
        },
        'discord': {
            'webhook_urls': ['YOUR_WEBHOOK_URL']
        },
        'min_alert_interval': 1
    }
    
    manager = AlertManager(config)
    
    # Test different alert types
    await manager.signal(
        "High velocity runner detected",
        token_symbol="PEPE",
        confidence=0.87,
        factors=["Volume spike 3x", "Holder growth +150/hr", "Progress acceleration"],
        details={'entry_price': 0.000123, 'target': 0.000234}
    )
    
    await manager.position_update(
        "Position closed with profit",
        token_symbol="DOGE",
        pnl_pct=25.5,
        details={'exit_price': 0.000234, 'holding_time': '2h 15m'}
    )
    
    await manager.critical(
        "Emergency stop triggered - large loss detected",
        details={'loss_usd': -5000, 'position': 'SHIB'}
    )
    
    await manager.close()


if __name__ == "__main__":
    asyncio.run(main())
