#!/usr/bin/env python3
"""
Telegram bot for server monitoring with LLM-powered status reports

To start the bot, run:
sudo -E bash -c "source venv/bin/activate && python3 main.py"
"""

import os
import logging
import io
import time
from typing import List
from datetime import datetime
import asyncio
import textwrap
import matplotlib
matplotlib.use('Agg')  # Use non-interactive backend
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from telegram.constants import ChatAction

from dotenv import load_dotenv
from utils.llm_tools import LLMTools
from utils.system_monitor import SystemMonitor

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Disable noisy loggers
logging.getLogger('httpx').setLevel(logging.WARNING)
logging.getLogger('telegram').setLevel(logging.WARNING)

class ServerStatusBot:
    def __init__(self):
        """Initialize the bot with required components"""
        self.bot_token = os.getenv('TELEGRAM_BOT_TOKEN')
        if not self.bot_token:
            raise ValueError("TELEGRAM_BOT_TOKEN environment variable is required")
        
        # Parse admin user IDs
        admins_str = os.getenv('ADMINS', '')
        self.admin_ids = set()
        if admins_str:
            try:
                self.admin_ids = {int(uid.strip()) for uid in admins_str.split(',') if uid.strip()}
            except ValueError as e:
                logger.error(f"Error parsing ADMINS environment variable: {e}")
                raise ValueError("ADMINS must contain comma-separated numeric user IDs")
        
        if not self.admin_ids:
            logger.warning("No admin users configured - bot will not respond to anyone")
        
        # Initialize components
        self.llm_tools = LLMTools()
        self.system_monitor = SystemMonitor(log_data=True)  # Enable data logging

        # Parse ignore patterns for plotting (filter out empty strings)
        self.ignore_plot_devices_temperature = [p.strip() for p in os.getenv('IGNORE_PLOT_DEVICES_TEMPERATURE', '').split(',') if p.strip()]
        self.ignore_plot_devices_disk = [p.strip() for p in os.getenv('IGNORE_PLOT_DEVICES_DISK', '').split(',') if p.strip()]
        self.ignore_plot_devices_io = [p.strip() for p in os.getenv('IGNORE_PLOT_DEVICES_IO', '').split(',') if p.strip()]
        self.ignore_plot_devices_gpu = [p.strip() for p in os.getenv('IGNORE_PLOT_DEVICES_GPU', '').split(',') if p.strip()]
        # Log ignore patterns if they exist
        if self.ignore_plot_devices_temperature:
            logger.info(f"Ignoring temperature devices: {', '.join(self.ignore_plot_devices_temperature)}")
        if self.ignore_plot_devices_disk:
            logger.info(f"Ignoring disk devices: {', '.join(self.ignore_plot_devices_disk)}")
        if self.ignore_plot_devices_io:
            logger.info(f"Ignoring I/O devices: {', '.join(self.ignore_plot_devices_io)}")
        if self.ignore_plot_devices_gpu:
            logger.info(f"Ignoring GPU devices: {', '.join(self.ignore_plot_devices_gpu)}")

        # Aliases for devices (given as alias::device,alias2::device2,etc.)
        self.aliases_temperature = os.getenv('ALIASES_TEMPERATURE', '').split(',')
        self.aliases_disk = os.getenv('ALIASES_DISK', '').split(',')
        self.aliases_io = os.getenv('ALIASES_IO', '').split(',')
        self.aliases_gpu = os.getenv('ALIASES_GPU', '').split(',')
        # Log aliases if they exist
        if self.aliases_temperature:
            logger.info(f"Aliases for temperature devices: {', '.join(self.aliases_temperature)}")
        if self.aliases_disk:
            logger.info(f"Aliases for disk devices: {', '.join(self.aliases_disk)}")
        if self.aliases_io:
            logger.info(f"Aliases for I/O devices: {', '.join(self.aliases_io)}")
        if self.aliases_gpu:
            logger.info(f"Aliases for GPU devices: {', '.join(self.aliases_gpu)}")

        # System monitoring configuration
        self.monitoring_enabled = os.getenv('MONITORING_ENABLED', 'true').lower() == 'true'
        self.monitoring_interval = int(os.getenv('MONITORING_INTERVAL', '300'))  # 5 minutes
        
        # RAID monitoring
        self.monitor_raid = os.getenv('MONITOR_RAID', 'true').lower() == 'true'
        
        # CPU monitoring
        self.monitor_cpu = os.getenv('MONITOR_CPU', 'true').lower() == 'true'
        self.cpu_threshold = float(os.getenv('CPU_THRESHOLD', '80.0'))  # %
        
        # General monitoring duration (used for CPU, temperature, etc.)
        self.monitor_duration = int(os.getenv('MONITOR_DURATION', '1200'))  # 20 minutes
        
        # Temperature monitoring
        self.monitor_temperature = os.getenv('MONITOR_TEMPERATURE', 'true').lower() == 'true'
        self.temperature_devices = [d.strip() for d in os.getenv('TEMPERATURE_DEVICES', 'coretemp,cpu,thermal').split(',') if d.strip()]
        self.temperature_threshold = float(os.getenv('TEMPERATURE_THRESHOLD', '60.0'))  # °C
        
        # GPU monitoring
        self.monitor_gpu = os.getenv('MONITOR_GPU', 'true').lower() == 'true'
        self.gpu_temperature_threshold = float(os.getenv('GPU_TEMPERATURE_THRESHOLD', '80.0'))  # °C
        self.gpu_usage_threshold = float(os.getenv('GPU_USAGE_THRESHOLD', '95.0'))  # %
        self.gpu_memory_threshold = float(os.getenv('GPU_MEMORY_THRESHOLD', '95.0'))  # %
        
        # Notification control
        self.notification_cooldown = int(os.getenv('NOTIFICATION_COOLDOWN', '3600'))  # 1 hour
        
        # Internal monitoring state
        self._monitoring_task = None
        self._last_notifications = {}  # Track last notification times
        self._cpu_high_start = None  # Track when high CPU started
        self._temperature_high_start = None  # Track when high temperature started
        self._gpu_high_temp_start = None  # Track when high GPU temperature started
        self._gpu_high_usage_start = None  # Track when high GPU usage started
        
        # Log monitoring configuration
        if self.monitoring_enabled:
            logger.info(f"System monitoring enabled:")
            logger.info(f"  - Check interval: {self.monitoring_interval}s")
            logger.info(f"  - RAID monitoring: {self.monitor_raid}")
            logger.info(f"  - CPU monitoring: {self.monitor_cpu} (>{self.cpu_threshold}% for {self.monitor_duration}s)")
            logger.info(f"  - Temperature monitoring: {self.monitor_temperature} (>{self.temperature_threshold}°C)")
            logger.info(f"  - Temperature devices: {', '.join(self.temperature_devices)}")
            logger.info(f"  - GPU monitoring: {self.monitor_gpu} (temp>{self.gpu_temperature_threshold}°C, usage>{self.gpu_usage_threshold}%, memory>{self.gpu_memory_threshold}%)")
            logger.info(f"  - Notification cooldown: {self.notification_cooldown}s")
        else:
            logger.info("System monitoring disabled")

        logger.info(f"Bot initialized with {len(self.admin_ids)} admin(s)")

    def apply_device_alias(self, device_name: str, aliases_list: List[str]) -> str:
        """
        Применяет алиас к имени устройства, если найден
        
        Args:
            device_name: Исходное имя устройства
            aliases_list: Список алиасов в формате "alias::device"
            
        Returns:
            Алиас устройства или исходное имя, если алиас не найден
        """
        if not aliases_list or not device_name:
            return device_name
            
        for alias_entry in aliases_list:
            if not alias_entry or '::' not in alias_entry:
                continue
                
            try:
                alias, device = alias_entry.split('::', 1)
                alias = alias.strip()
                device = device.strip()
                
                # Проверяем точное совпадение или вхождение устройства в имя
                if device == device_name or device in device_name:
                    return alias
            except ValueError:
                # Неверный формат алиаса, пропускаем
                continue
                
        return device_name

    def apply_aliases_to_status(self, status_text: str) -> str:
        """
        Применяет алиасы устройств к тексту статуса
        
        Args:
            status_text: Исходный текст статуса
            
        Returns:
            Текст статуса с примененными алиасами
        """
        if not status_text:
            return status_text
            
        result_text = status_text
        
        # Применяем алиасы температурных устройств
        for alias_entry in self.aliases_temperature:
            if not alias_entry or '::' not in alias_entry:
                continue
            try:
                alias, device = alias_entry.split('::', 1)
                alias = alias.strip()
                device = device.strip()
                if device:
                    result_text = result_text.replace(device, alias)
            except ValueError:
                continue
        
        # Применяем алиасы дисковых устройств  
        for alias_entry in self.aliases_disk:
            if not alias_entry or '::' not in alias_entry:
                continue
            try:
                alias, device = alias_entry.split('::', 1)
                alias = alias.strip()
                device = device.strip()
                if device:
                    result_text = result_text.replace(device, alias)
            except ValueError:
                continue
                
        # Применяем алиасы I/O устройств
        for alias_entry in self.aliases_io:
            if not alias_entry or '::' not in alias_entry:
                continue
            try:
                alias, device = alias_entry.split('::', 1)
                alias = alias.strip()
                device = device.strip()
                if device:
                    result_text = result_text.replace(device, alias)
            except ValueError:
                continue
                
        # Применяем алиасы GPU устройств
        for alias_entry in self.aliases_gpu:
            if not alias_entry or '::' not in alias_entry:
                continue
            try:
                alias, device = alias_entry.split('::', 1)
                alias = alias.strip()
                device = device.strip()
                if device:
                    result_text = result_text.replace(device, alias)
            except ValueError:
                continue
                
        return result_text

    def rewrite(self, text: str) -> str:
        """Rewrite text with LLM"""
        try:
            return self.llm_tools.rewrite(text)
        except KeyboardInterrupt:
            raise KeyboardInterrupt
        except Exception as e:
            logger.error(f"Error rewriting text: {e}")
            return text
    
    async def escaping(self, text: str) -> str:
        """
        Escape text for MarkdownV2 format.
        Inside (...) part of inline link definition, all ')' and '\' must be escaped with a preceding '\' character.
        In all other places characters:
        '_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!' 
        must be escaped with the preceding character '\'.
        """
        escaped = text.translate(str.maketrans({
            "-": r"\-", "]": r"\]", "^": r"\^", "$": r"\$", "*": r"\*", ".": r"\.", "!": r"\!",
            "_": r"\_", "[": r"\[", "(": r"\(", ")": r"\)", "~": r"\~", "`": r"\`", ">": r"\>",
            "#": r"\#", "+": r"\+", "=": r"\=", "|": r"\|", "{": r"\{", "}": r"\}",
        }))
        return escaped
    
    async def send_message(self, update: Update, text: str, max_length: int = 4096, markdown: int = 0, message_reply: bool = True):
        """
        Send a message to user, if too long - send it in parts
        Args:
            update: Telegram update object
            text: Text to send
            max_length: Maximum length per message (default 4096)
            markdown: Markdown mode (0=none, 1=Markdown, 2=MarkdownV2)
            message_reply: Whether to reply to the original message
        """
        try:
            # Split text into parts
            parts = [text[i:i+max_length] for i in range(0, len(text), max_length)]
            print(f'Text length: {len(text)}. Split into {len(parts)} parts (markdown={markdown}).')
            
            # Send each part
            for index, part in enumerate(parts):
                if markdown == 0:
                    await update.message.reply_text(
                        part, 
                        reply_to_message_id=update.message.message_id if index == 0 and message_reply else None
                    )
                elif markdown == 1:
                    try:
                        await update.message.reply_text(
                            part, 
                            parse_mode='Markdown',
                            reply_to_message_id=update.message.message_id if index == 0 and message_reply else None
                        )
                    except Exception as e:
                        print(f'Error sending message (Markdown - {e}): {part[:100]}...')
                        await self.send_message(update, part, max_length, markdown=2, message_reply=message_reply and index == 0)
                elif markdown == 2:
                    try:
                        esc_part = await self.escaping(part)
                        await update.message.reply_text(
                            esc_part, 
                            parse_mode='MarkdownV2',
                            reply_to_message_id=update.message.message_id if index == 0 and message_reply else None
                        )
                    except Exception as e:
                        print(f'Error sending message (MarkdownV2 - {e}): {part[:100]}...')
                        await update.message.reply_text(
                            part, 
                            reply_to_message_id=update.message.message_id if index == 0 and message_reply else None
                        )
                else:
                    # If markdown is not 0, 1 or 2, send message without markdown
                    await update.message.reply_text(
                        part, 
                        reply_to_message_id=update.message.message_id if index == 0 and message_reply else None
                    )
        except Exception as e:
            print(f'Could not send message to user: {update.effective_user.id} - {e}')
    
    def is_admin(self, user_id: int) -> bool:
        """Check if user is in admin list"""
        return user_id in self.admin_ids
    
    def _should_send_notification(self, notification_type: str) -> bool:
        """Check if notification should be sent based on cooldown"""
        now = time.time()
        last_sent = self._last_notifications.get(notification_type, 0)
        return (now - last_sent) >= self.notification_cooldown
    
    def _mark_notification_sent(self, notification_type: str):
        """Mark notification as sent"""
        self._last_notifications[notification_type] = time.time()
    
    async def _send_alert(self, application: Application, message: str, alert_type: str) -> None:
        """Send alert message to all admins"""
        if not self._should_send_notification(alert_type):
            logger.debug(f"Skipping {alert_type} notification due to cooldown")
            return
        
        # Rewrite message with LLM
        processed_message = self.rewrite(message)
        
        sent_count = 0
        failed_count = 0
        
        for admin_id in self.admin_ids:
            try:
                await application.bot.send_message(
                    chat_id=admin_id,
                    text=processed_message,
                    parse_mode='Markdown'
                )
                sent_count += 1
                await asyncio.sleep(0.1)  # Small delay to avoid rate limits
                
            except Exception as e:
                failed_count += 1
                logger.warning(f"Failed to send alert to admin {admin_id}: {e}")
        
        if sent_count > 0:
            self._mark_notification_sent(alert_type)
            logger.info(f"Alert sent: {alert_type} ({sent_count} sent, {failed_count} failed)")
    
    async def _check_system_health(self, application: Application) -> None:
        """Check system health and send alerts if needed"""
        try:
            current_time = time.time()
            
            # Check RAID status
            if self.monitor_raid:
                raid_status = self.system_monitor.raid_good
                if raid_status is False:  # RAID has problems
                    alert_message = textwrap.dedent("""
                        🚨 **RAID ALERT**
                        
                        ❌ **Status:** RAID problems detected!
                        🕐 **Time:** {timestamp}
                        
                        One or more RAID arrays have issues. Check server immediately!
                        Use /status for detailed information.
                    """).format(timestamp=datetime.now().strftime('%Y-%m-%d %H:%M:%S')).strip()
                    
                    print(f"🔥 Alert: {alert_message}")
                    await self._send_alert(application, alert_message, "raid_failed")
            
            # Check CPU usage
            if self.monitor_cpu:
                cpu_data = self.system_monitor.get_cpu_memory_load()
                if 'cpu' in cpu_data:
                    current_cpu = cpu_data['cpu'].get('percent_total', 0)
                    
                    if current_cpu > self.cpu_threshold:
                        # High CPU detected
                        if self._cpu_high_start is None:
                            self._cpu_high_start = current_time
                        elif (current_time - self._cpu_high_start) >= self.monitor_duration:
                            # High CPU for too long
                            duration_minutes = int((current_time - self._cpu_high_start) / 60)
                            
                            alert_message = textwrap.dedent("""
                                🚨 **HIGH CPU ALERT**
                                
                                ⚠️ **Current CPU:** {cpu:.1f}%
                                ⏱️ **Duration:** {duration} minutes
                                🔥 **Threshold:** {threshold}% for {max_duration} minutes
                                🕐 **Time:** {timestamp}
                                
                                Server CPU usage has been high for an extended period!
                                Use /status for detailed information.
                            """).format(
                                cpu=current_cpu,
                                duration=duration_minutes,
                                threshold=self.cpu_threshold,
                                max_duration=int(self.monitor_duration / 60),
                                timestamp=datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                            ).strip()
                            
                            print(f"🔥 Alert: {alert_message}")
                            await self._send_alert(application, alert_message, "cpu_high")
                    else:
                        # CPU back to normal
                        self._cpu_high_start = None
            
            # Check temperatures
            if self.monitor_temperature:
                temp_data = self.system_monitor.get_temperatures()
                high_temps = []
                
                # Check psutil temperatures
                for sensor_name, sensor_list in temp_data.items():
                    if sensor_name.endswith('_error') or sensor_name in ['lm_sensors', 'thermal_zones']:
                        continue
                    if isinstance(sensor_list, list):
                        for sensor in sensor_list:
                            if 'current' in sensor:
                                # Check if this device should be monitored
                                label = sensor.get('label', sensor_name)
                                device_monitored = any(device.lower() in label.lower() or device.lower() in sensor_name.lower() 
                                                     for device in self.temperature_devices)
                                
                                if device_monitored and sensor['current'] > self.temperature_threshold:
                                    high_temps.append({
                                        'device': f"{sensor_name}_{label}",
                                        'temperature': sensor['current'],
                                        'critical': sensor.get('critical'),
                                        'high': sensor.get('high')
                                    })
                
                # Check thermal zones
                if 'thermal_zones' in temp_data and temp_data['thermal_zones']:
                    for zone in temp_data['thermal_zones']:
                        zone_monitored = any(device.lower() in zone['type'].lower() 
                                           for device in self.temperature_devices)
                        
                        if zone_monitored and zone['temperature'] > self.temperature_threshold:
                            high_temps.append({
                                'device': zone['type'],
                                'temperature': zone['temperature'],
                                'critical': None,
                                'high': None
                            })
                
                if high_temps:
                    # High temperature detected
                    if self._temperature_high_start is None:
                        self._temperature_high_start = current_time
                    elif (current_time - self._temperature_high_start) >= self.monitor_duration:
                        # High temperature for too long
                        duration_minutes = int((current_time - self._temperature_high_start) / 60)
                        
                        temp_details = []
                        for temp in high_temps[:5]:  # Limit to 5 devices
                            limits = ""
                            if temp['critical'] or temp['high']:
                                limits = f" (high: {temp['high'] or 'N/A'}, critical: {temp['critical'] or 'N/A'})"
                            temp_details.append(f"• **{temp['device']}:** {temp['temperature']:.1f}°C{limits}")
                        
                        alert_message = textwrap.dedent("""
                            🌡️ **HIGH TEMPERATURE ALERT**
                            
                            🔥 **Threshold:** {threshold}°C exceeded for {duration} minutes
                            ⏱️ **Duration:** {duration} minutes
                            🕐 **Time:** {timestamp}
                            
                            **High temperatures detected:**
                            {temp_list}
                            
                            Check server cooling system immediately!
                            Use /status for detailed information.
                        """).format(
                            threshold=self.temperature_threshold,
                            duration=duration_minutes,
                            timestamp=datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                            temp_list='\n'.join(temp_details)
                        ).strip()
                        
                        await self._send_alert(application, alert_message, "temperature_high")
                else:
                    # Temperature back to normal
                    self._temperature_high_start = None
            
            # Check GPU status (only if GPU monitoring enabled and GPU available)
            if self.monitor_gpu and self.system_monitor.nvidia_smi_available:
                gpu_data = self.system_monitor.get_gpu_status()
                if 'gpus' in gpu_data and gpu_data['gpus']:
                    high_gpu_temps = []
                    high_gpu_usage = []
                    
                    for gpu in gpu_data['gpus']:
                        gpu_name = gpu.get('name', f"GPU {gpu['id']}")
                        
                        # Check GPU temperature
                        gpu_temp = gpu.get('temperature')
                        if gpu_temp and gpu_temp > self.gpu_temperature_threshold:
                            high_gpu_temps.append({
                                'gpu': gpu_name,
                                'temperature': gpu_temp,
                                'id': gpu['id']
                            })
                        
                        # Check GPU usage and memory
                        gpu_util = gpu.get('utilization_gpu')
                        gpu_mem_util = gpu.get('utilization_memory')
                        memory_used = gpu.get('memory_used')
                        memory_total = gpu.get('memory_total')
                        
                        memory_percent = 0
                        if memory_used and memory_total and memory_total > 0:
                            memory_percent = (memory_used / memory_total) * 100
                        
                        if ((gpu_util and gpu_util > self.gpu_usage_threshold) or 
                            (memory_percent > self.gpu_memory_threshold)):
                            high_gpu_usage.append({
                                'gpu': gpu_name,
                                'gpu_usage': gpu_util or 0,
                                'memory_usage': gpu_mem_util or 0,
                                'memory_percent': memory_percent,
                                'id': gpu['id']
                            })
                    
                    # Handle high GPU temperature alerts
                    if high_gpu_temps:
                        if self._gpu_high_temp_start is None:
                            self._gpu_high_temp_start = current_time
                        elif (current_time - self._gpu_high_temp_start) >= self.monitor_duration:
                            duration_minutes = int((current_time - self._gpu_high_temp_start) / 60)
                            
                            temp_details = []
                            for gpu_temp in high_gpu_temps[:3]:  # Limit to 3 GPUs
                                temp_details.append(f"• **{gpu_temp['gpu']}:** {gpu_temp['temperature']:.1f}°C")
                            
                            alert_message = textwrap.dedent("""
                                🎮🌡️ **HIGH GPU TEMPERATURE ALERT**
                                
                                🔥 **Threshold:** {threshold}°C exceeded for {duration} minutes
                                ⏱️ **Duration:** {duration} minutes
                                🕐 **Time:** {timestamp}
                                
                                **High GPU temperatures detected:**
                                {temp_list}
                                
                                Check GPU cooling system immediately!
                                Use /status for detailed information.
                            """).format(
                                threshold=self.gpu_temperature_threshold,
                                duration=duration_minutes,
                                timestamp=datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                                temp_list='\n'.join(temp_details)
                            ).strip()
                            
                            await self._send_alert(application, alert_message, "gpu_temperature_high")
                    else:
                        self._gpu_high_temp_start = None
                    
                    # Handle high GPU usage alerts
                    if high_gpu_usage:
                        if self._gpu_high_usage_start is None:
                            self._gpu_high_usage_start = current_time
                        elif (current_time - self._gpu_high_usage_start) >= self.monitor_duration:
                            duration_minutes = int((current_time - self._gpu_high_usage_start) / 60)
                            
                            usage_details = []
                            for gpu_usage in high_gpu_usage[:3]:  # Limit to 3 GPUs
                                details = f"• **{gpu_usage['gpu']}:** {gpu_usage['gpu_usage']:.1f}% usage"
                                if gpu_usage['memory_percent'] > 0:
                                    details += f", {gpu_usage['memory_percent']:.1f}% memory"
                                usage_details.append(details)
                            
                            alert_message = textwrap.dedent("""
                                🎮⚡ **HIGH GPU USAGE ALERT**
                                
                                ⚠️ **Usage/Memory Thresholds:** {gpu_threshold}%/{memory_threshold}% exceeded for {duration} minutes
                                ⏱️ **Duration:** {duration} minutes
                                🕐 **Time:** {timestamp}
                                
                                **High GPU usage detected:**
                                {usage_list}
                                
                                Check GPU workload and performance!
                                Use /status for detailed information.
                            """).format(
                                gpu_threshold=self.gpu_usage_threshold,
                                memory_threshold=self.gpu_memory_threshold,
                                duration=duration_minutes,
                                timestamp=datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                                usage_list='\n'.join(usage_details)
                            ).strip()
                            
                            await self._send_alert(application, alert_message, "gpu_usage_high")
                    else:
                        self._gpu_high_usage_start = None
                    
        except Exception as e:
            logger.error(f"Error checking system health: {e}")
    
    async def _monitoring_worker(self, application: Application) -> None:
        """Background monitoring worker"""
        logger.info("System monitoring started")
        
        while True:
            try:
                await self._check_system_health(application)
                await asyncio.sleep(self.monitoring_interval)
            except asyncio.CancelledError:
                logger.info("System monitoring stopped")
                break
            except Exception as e:
                logger.error(f"Error in monitoring worker: {e}")
                await asyncio.sleep(60)  # Wait 1 minute before retrying
    
    def start_monitoring(self, application: Application):
        """Start system monitoring"""
        if self.monitoring_enabled and self._monitoring_task is None:
            self._monitoring_task = asyncio.create_task(self._monitoring_worker(application))
            logger.info("System monitoring task created")
    
    def stop_monitoring(self):
        """Stop system monitoring"""
        if self._monitoring_task:
            self._monitoring_task.cancel()
            self._monitoring_task = None
            logger.info("System monitoring task stopped")
    
    def create_system_plots(self) -> io.BytesIO:
        """Create system monitoring plots and return as BytesIO buffer"""
        # Get logged data
        logged_data = self.system_monitor.get_logged_data()
        
        # Check if we have any data
        has_data = any(len(data_list) > 0 for data_list in logged_data.values())
        
        # Check if we have enough data points and time range
        total_points = sum(len(data_list) for data_list in logged_data.values())
        time_range = 0
        
        if has_data:
            all_timestamps = []
            for data_list in logged_data.values():
                if data_list:
                    all_timestamps.extend([entry['timestamp'] for entry in data_list])
            
            if all_timestamps:
                time_range = max(all_timestamps) - min(all_timestamps)
        
        # If no data or insufficient time range, return None to send text message instead
        if not has_data or time_range < 30:  # Less than 30 seconds of data
            return None
        else:
            # Set modern dark theme style
            plt.style.use('dark_background')
            
            # Create subplots for different metrics
            fig, axes = plt.subplots(2, 2, figsize=(16, 12))
            fig.patch.set_facecolor('#1e1e1e')  # Dark background
            
            # Modern color palette
            colors_primary = ['#00d4aa', '#ff6b6b', '#4ecdc4', '#45b7d1', '#96ceb4', '#feca57']
            colors_secondary = ['#00a085', '#ff5252', '#26a69a', '#1e88e5', '#66bb6a', '#ffb300']
            
            fig.suptitle('System Monitoring Dashboard', fontsize=20, fontweight='bold', 
                        color='white', y=0.98)
            
            # Subplot 1: CPU, Memory and GPU Usage
            ax1 = axes[0, 0]
            ax1.set_facecolor('#2d2d2d')
            
            color_index = 0
            
            if logged_data['cpu_usage_data']:
                cpu_timestamps = [datetime.fromtimestamp(d['timestamp']) for d in logged_data['cpu_usage_data']]
                cpu_values = [d['usage'] for d in logged_data['cpu_usage_data']]
                # Use markers for better visibility with few data points
                marker_style = 'o' if len(cpu_values) < 10 else None
                ax1.plot(cpu_timestamps, cpu_values, color='#2196f3', label='CPU Usage (%)', 
                        linewidth=2, marker=marker_style, markersize=6, alpha=0.9,
                        markerfacecolor='#2196f3', markeredgecolor='white', markeredgewidth=1)
            
            if logged_data['memory_usage_data']:
                mem_timestamps = [datetime.fromtimestamp(d['timestamp']) for d in logged_data['memory_usage_data']]
                mem_values = [d['usage'] for d in logged_data['memory_usage_data']]
                marker_style = 's' if len(mem_values) < 10 else None
                ax1.plot(mem_timestamps, mem_values, color='#1976d2', label='Memory Usage (%)', 
                        linewidth=2, marker=marker_style, markersize=6, alpha=0.9, linestyle='--',
                        markerfacecolor='#1976d2', markeredgecolor='white', markeredgewidth=1)
            
            # Add GPU usage data
            if logged_data['gpu_data']:
                # Filter and group GPU data
                gpu_by_id = {}
                for entry in logged_data['gpu_data']:
                    gpu_id = entry['gpu_id']
                    # Skip entries with unavailable status or ignored devices
                    if (entry.get('status') == 'nvidia-smi_unavailable' or gpu_id == 'N/A' or
                        any(ignore_pattern in str(gpu_id) for ignore_pattern in self.ignore_plot_devices_gpu)):
                        continue
                    # Skip entries with no actual data
                    if entry.get('gpu_usage') is None and entry.get('memory_usage') is None:
                        continue
                        
                    gpu_key = f"GPU{gpu_id}"
                    if gpu_key not in gpu_by_id:
                        gpu_by_id[gpu_key] = {
                            'timestamps': [], 
                            'gpu_usage': [], 
                            'memory_usage': []
                        }
                    gpu_by_id[gpu_key]['timestamps'].append(datetime.fromtimestamp(entry['timestamp']))
                    gpu_by_id[gpu_key]['gpu_usage'].append(entry.get('gpu_usage', 0) or 0)
                    gpu_by_id[gpu_key]['memory_usage'].append(entry.get('memory_usage', 0) or 0)
                
                # Plot GPU data
                markers = ['^', 'D', 'v', '<', '>', 'P']
                # GPU color palette: green base colors for each GPU
                gpu_colors = ['#4caf50', '#ff9800', '#9c27b0', '#f44336', '#795548', '#607d8b']
                
                for i, (gpu_name, data) in enumerate(gpu_by_id.items()):
                    marker_style = markers[i % len(markers)] if len(data['gpu_usage']) < 10 else None
                    # Apply aliases for display
                    display_gpu = self.apply_device_alias(gpu_name, self.aliases_gpu)
                    
                    # Use same color for both usage and memory for each GPU
                    gpu_color = gpu_colors[i % len(gpu_colors)]
                    
                    # GPU usage line (solid)
                    ax1.plot(data['timestamps'], data['gpu_usage'], 
                            color=gpu_color, linestyle='-', 
                            label=f'{display_gpu} Usage (%)', linewidth=2,
                            marker=marker_style, markersize=5, alpha=0.9,
                            markerfacecolor=gpu_color, 
                            markeredgecolor='white', markeredgewidth=1)
                    
                    # GPU memory utilization line (dashed, same color)
                    ax1.plot(data['timestamps'], data['memory_usage'], 
                            color=gpu_color, linestyle='--', 
                            label=f'{display_gpu} Memory (%)', linewidth=2,
                            marker=marker_style, markersize=5, alpha=0.9,
                            markerfacecolor=gpu_color, 
                            markeredgecolor='white', markeredgewidth=1)
            
            ax1.set_title('CPU, Memory & GPU Usage', fontweight='bold', fontsize=14, color='white', pad=20)
            ax1.set_ylabel('Usage (%)', fontsize=12, color='white')
            ax1.legend(fontsize=10, fancybox=True, shadow=True, framealpha=0.9)
            ax1.grid(True, alpha=0.2, linestyle='--', color='gray')
            ax1.set_ylim(0, 100)
            ax1.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M'))
            ax1.tick_params(colors='white', labelsize=10)
            
            # Subplot 2: System and GPU Temperatures
            ax2 = axes[0, 1]
            ax2.set_facecolor('#2d2d2d')
            
            temp_color_index = 0
            temp_colors = ['#00bcd4', '#4caf50', '#ff9800', '#f44336', '#9c27b0', '#795548', '#607d8b', '#e91e63']
            markers = ['o', 's', '^', 'D', 'v', '<', '>', 'P']
            
            if logged_data['temperature_data']:
                # Group by label
                temp_by_label = {}
                for entry in logged_data['temperature_data']:
                    label = entry['label']
                    # Skip ignored temperature devices and no_data entries
                    if (label == 'no_data' or entry['temperature'] is None or 
                        any(ignore_pattern in label for ignore_pattern in self.ignore_plot_devices_temperature)):
                        continue
                    if label not in temp_by_label:
                        temp_by_label[label] = {'timestamps': [], 'temperatures': []}
                    temp_by_label[label]['timestamps'].append(datetime.fromtimestamp(entry['timestamp']))
                    temp_by_label[label]['temperatures'].append(entry['temperature'])
                
                for label, data in temp_by_label.items():
                    color = temp_colors[temp_color_index % len(temp_colors)]
                    marker_style = markers[temp_color_index % len(markers)] if len(data['temperatures']) < 10 else None
                    # Применяем алиас для отображения в легенде графика
                    display_label = self.apply_device_alias(label, self.aliases_temperature)
                    ax2.plot(data['timestamps'], data['temperatures'], 
                            color=color, label=display_label, linewidth=2, 
                            marker=marker_style, markersize=5, alpha=0.9,
                            markerfacecolor=color, markeredgecolor='white', markeredgewidth=1)
                    temp_color_index += 1
            
            # Add GPU temperature data
            if logged_data['gpu_data']:
                # Filter and group GPU temperature data
                gpu_temp_by_id = {}
                for entry in logged_data['gpu_data']:
                    gpu_id = entry['gpu_id']
                    # Skip entries with unavailable status or ignored devices
                    if (entry.get('status') == 'nvidia-smi_unavailable' or gpu_id == 'N/A' or
                        any(ignore_pattern in str(gpu_id) for ignore_pattern in self.ignore_plot_devices_gpu)):
                        continue
                    # Skip entries with no temperature data
                    if entry.get('temperature') is None:
                        continue
                        
                    gpu_key = f"GPU{gpu_id}"
                    if gpu_key not in gpu_temp_by_id:
                        gpu_temp_by_id[gpu_key] = {
                            'timestamps': [], 
                            'temperature': []
                        }
                    gpu_temp_by_id[gpu_key]['timestamps'].append(datetime.fromtimestamp(entry['timestamp']))
                    gpu_temp_by_id[gpu_key]['temperature'].append(entry.get('temperature') or 0)
                
                # Plot GPU temperature data
                for gpu_name, data in gpu_temp_by_id.items():
                    color = temp_colors[temp_color_index % len(temp_colors)]
                    marker_style = markers[temp_color_index % len(markers)] if len(data['temperature']) < 10 else None
                    # Apply aliases for display
                    display_gpu = self.apply_device_alias(gpu_name, self.aliases_gpu)
                    
                    ax2.plot(data['timestamps'], data['temperature'], 
                            color=color, linestyle='-', label=f'{display_gpu} Temp', linewidth=2,
                            marker=marker_style, markersize=5, alpha=0.9,
                            markerfacecolor=color, markeredgecolor='white', markeredgewidth=1)
                    temp_color_index += 1
            
            ax2.set_title('System & GPU Temperatures', fontweight='bold', fontsize=14, color='white', pad=20)
            ax2.set_ylabel('Temperature (°C)', fontsize=12, color='white')
            ax2.legend(fontsize=9, fancybox=True, shadow=True, framealpha=0.9)
            ax2.grid(True, alpha=0.2, linestyle='--', color='gray')
            ax2.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M'))
            ax2.tick_params(colors='white', labelsize=10)
            
            # Subplot 3: Disk Usage  
            ax3 = axes[1, 0]
            ax3.set_facecolor('#2d2d2d')
            
            if logged_data['disk_usage_data']:
                # Group by device
                disk_by_device = {}
                for entry in logged_data['disk_usage_data']:
                    device_key = f"{entry['device']} ({entry['mount']})"
                    # Skip ignored disk devices and no_data entries
                    if (entry['device'] == 'no_data' or entry['usage'] is None or
                        any(ignore_pattern in entry['device'] or ignore_pattern in entry['mount']
                           for ignore_pattern in self.ignore_plot_devices_disk)):
                        continue
                    if device_key not in disk_by_device:
                        disk_by_device[device_key] = {'timestamps': [], 'usage': []}
                    disk_by_device[device_key]['timestamps'].append(datetime.fromtimestamp(entry['timestamp']))
                    disk_by_device[device_key]['usage'].append(entry['usage'])
                
                # Disk-specific colors (storage themed)
                disk_colors = ['#3f51b5', '#009688', '#ff5722', '#ffc107', '#9c27b0', '#607d8b']
                markers = ['o', 's', '^', 'D', 'v', '<', '>', 'P']
                for i, (device, data) in enumerate(disk_by_device.items()):
                    color = disk_colors[i % len(disk_colors)]
                    marker_style = markers[i % len(markers)] if len(data['usage']) < 10 else None
                    # Применяем алиас для отображения в легенде графика
                    display_device = self.apply_device_alias(device, self.aliases_disk)
                    ax3.plot(data['timestamps'], data['usage'], 
                            color=color, label=display_device, linewidth=2,
                            marker=marker_style, markersize=5, alpha=0.9,
                            markerfacecolor=color, markeredgecolor='white', markeredgewidth=1)
            
            ax3.set_title('Disk Usage', fontweight='bold', fontsize=14, color='white', pad=20)
            ax3.set_ylabel('Usage (%)', fontsize=12, color='white')
            ax3.legend(fontsize=9, fancybox=True, shadow=True, framealpha=0.9)
            ax3.grid(True, alpha=0.2, linestyle='--', color='gray')
            ax3.set_ylim(0, 100)
            ax3.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M'))
            ax3.tick_params(colors='white', labelsize=10)
            
            # Subplot 4: Disk I/O
            ax4 = axes[1, 1]
            ax4.set_facecolor('#2d2d2d')
            
            if logged_data['disk_io_data']:
                # Group by device and show read/write bytes
                io_by_device = {}
                for entry in logged_data['disk_io_data']:
                    device = entry['device']
                    # Skip ignored I/O devices and no_data entries
                    if (device == 'no_data' or 
                        any(ignore_pattern in device for ignore_pattern in self.ignore_plot_devices_io)):
                        continue
                    if device not in io_by_device:
                        io_by_device[device] = {
                            'timestamps': [], 
                            'read_mb': [], 
                            'write_mb': []
                        }
                    io_by_device[device]['timestamps'].append(datetime.fromtimestamp(entry['timestamp']))
                    # Convert bytes to MB
                    io_by_device[device]['read_mb'].append(entry['read_bytes'] / (1024*1024))
                    io_by_device[device]['write_mb'].append(entry['write_bytes'] / (1024*1024))
                
                # Calculate delta between consecutive measurements for each device
                for device, data in io_by_device.items():
                    if len(data['read_mb']) > 1:
                        # Calculate delta between consecutive measurements
                        read_deltas = [0]  # First measurement delta is 0
                        write_deltas = [0]  # First measurement delta is 0
                        
                        for i in range(1, len(data['read_mb'])):
                            read_delta = data['read_mb'][i] - data['read_mb'][i-1]
                            write_delta = data['write_mb'][i] - data['write_mb'][i-1]
                            read_deltas.append(read_delta)
                            write_deltas.append(write_delta)
                        
                        # Replace with delta values
                        data['read_mb'] = read_deltas
                        data['write_mb'] = write_deltas
                    elif len(data['read_mb']) == 1:
                        # Single measurement - delta is 0
                        data['read_mb'] = [0]
                        data['write_mb'] = [0]
                
                # I/O specific colors with read/write distinction
                io_colors = ['#2196f3', '#4caf50', '#ff9800', '#e91e63', '#9c27b0', '#795548']
                markers = ['o', 's', '^', 'D', 'v', '<']
                for i, (device, data) in enumerate(io_by_device.items()):
                    base_color = io_colors[i % len(io_colors)]
                    marker_style = markers[i % len(markers)] if len(data['read_mb']) < 10 else None
                    # Применяем алиас для отображения в легенде графика
                    display_device = self.apply_device_alias(device, self.aliases_io)
                    
                    # Read line (solid)
                    ax4.plot(data['timestamps'], data['read_mb'], 
                            color=base_color, linestyle='-', label=f'{display_device} Read', linewidth=2,
                            marker=marker_style, markersize=5, alpha=0.9,
                            markerfacecolor=base_color, markeredgecolor='white', markeredgewidth=1)
                    
                    # Write line (dashed, same color)
                    ax4.plot(data['timestamps'], data['write_mb'], 
                            color=base_color, linestyle='--', label=f'{display_device} Write', linewidth=2,
                            marker=marker_style, markersize=5, alpha=0.9,
                            markerfacecolor=base_color, markeredgecolor='white', markeredgewidth=1)
            
            ax4.set_title('Disk I/O Activity', fontweight='bold', fontsize=14, color='white', pad=20)
            ax4.set_ylabel('Δ (MB)', fontsize=12, color='white')
            ax4.legend(fontsize=9, fancybox=True, shadow=True, framealpha=0.9)
            ax4.grid(True, alpha=0.2, linestyle='--', color='gray')
            ax4.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M'))
            ax4.tick_params(colors='white', labelsize=10)
            
            # Synchronize time axes for all subplots
            if has_data:
                # Find overall time range from all data
                all_timestamps = []
                for data_list in logged_data.values():
                    if data_list:
                        all_timestamps.extend([entry['timestamp'] for entry in data_list])
                
                if all_timestamps:
                    min_time = datetime.fromtimestamp(min(all_timestamps))
                    max_time = datetime.fromtimestamp(max(all_timestamps))
                    
                    # Set same time range for all subplots
                    for ax in [ax1, ax2, ax3, ax4]:
                        ax.set_xlim(min_time, max_time)
            
            # Format x-axis for all subplots
            for ax in [ax1, ax2, ax3, ax4]:
                ax.tick_params(axis='x', rotation=45, colors='white')
                # Add subtle border
                for spine in ax.spines.values():
                    spine.set_edgecolor('#555555')
                    spine.set_linewidth(1)
            
            # Add info text if we have very few data points
            if total_points < 20:
                fig.text(0.02, 0.02, f'Limited data: {total_points} points, {time_range:.0f}s range', 
                        fontsize=11, style='italic', alpha=0.8, color='#cccccc',
                        bbox=dict(boxstyle="round,pad=0.3", facecolor='#333333', alpha=0.8))
        
        # Adjust layout and save to buffer
        plt.tight_layout(pad=3.0)  # More padding for better spacing
        
        # Save to BytesIO buffer with higher quality
        buffer = io.BytesIO()
        plt.savefig(buffer, format='png', dpi=200, bbox_inches='tight', 
                   facecolor='#1e1e1e', edgecolor='none')
        buffer.seek(0)
        plt.close()  # Important: close the figure to free memory
        
        return buffer
    
    async def send_startup_notification(self, application: Application) -> None:
        """Send startup notification to all admins"""
        startup_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        # Get basic system info for startup message
        try:
            uptime_data = self.system_monitor.get_uptime()
            load_data = self.system_monitor.get_cpu_memory_load()
            
            startup_message = textwrap.dedent(f"""
                🚀 **Server Monitor Bot Started**

                ⏰ **Start Time:** {startup_time}
                🖥️ **System Uptime:** {uptime_data.get('uptime_formatted', 'Unknown')}
                💾 **Memory Usage:** {load_data.get('memory', {}).get('percent', 0):.1f}%

                **Available commands:**
                • /status - Get detailed system information
                • /plot - Generate system monitoring graphs
                • /top - Show top 10 CPU-consuming processes
                • /info - Show configuration and alerting status
                • /start - Get help message

                Bot is now monitoring the server and ready to serve!
            """).strip()
            
        except Exception as e:
            logger.error(f"Error collecting system info for startup: {e}")
            startup_message = textwrap.dedent(f"""
                🚀 **Server Monitor Bot Started**

                ⏰ **Start Time:** {startup_time}
                ❌ **Error:** {e}

                **Available commands:**
                • /status - Get system information
                • /plot - Generate monitoring graphs
                • /top - Show top CPU processes
                • /info - Show configuration status
                • /start - Get help message

                Bot is now online and ready to monitor the server!
            """).strip()
            
        # Rewrite with LLM
        startup_message = self.rewrite(startup_message)
        
        # Send notification to all admins
        sent_count = 0
        failed_count = 0
        
        for admin_id in self.admin_ids:
            try:
                await application.bot.send_message(
                    chat_id=admin_id,
                    text=startup_message,
                    parse_mode='Markdown'
                )
                sent_count += 1
                logger.info(f"Startup notification sent to admin {admin_id}")
                
                # Small delay to avoid rate limits
                await asyncio.sleep(0.5)
                
            except Exception as e:
                failed_count += 1
                logger.warning(f"Failed to send startup notification to admin {admin_id}: {e}")
        
        logger.info(f"Startup notifications: {sent_count} sent, {failed_count} failed")
    
    async def status_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /status command"""
        user_id = update.effective_user.id
        username = update.effective_user.username or "Unknown"
        
        logger.info(f"Status command received from user {user_id} (@{username})")
        
        # Check if user is admin
        if not self.is_admin(user_id):
            logger.warning(f"Unauthorized access attempt from user {user_id} (@{username})")
            await update.message.reply_text(
                "🚫 Access denied. You are not authorized to use this bot."
            )
            return
        
        # Show typing indicator
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
        
        try:
            # Get system status
            logger.info("Collecting system status...")
            raw_status = self.system_monitor.get_human_readable_status()
            
            # Apply device aliases to status
            status_with_aliases = self.apply_aliases_to_status(raw_status)
            
            # Try to rewrite with LLM
            await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
            processed_status = self.rewrite(status_with_aliases)
            await self.send_message(update, processed_status, markdown=1)
                
        except Exception as e:
            logger.error(f"Error getting system status: {e}")
            await update.message.reply_text(
                f"❌ Error collecting system status: {str(e)}"
            )
    
    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /start command"""
        user_id = update.effective_user.id
        username = update.effective_user.username or "Unknown"
        
        logger.info(f"Start command received from user {user_id} (@{username})")
        
        if not self.is_admin(user_id):
            await update.message.reply_text(
                "🚫 Access denied. You are not authorized to use this bot."
            )
            return
        
        welcome_message = textwrap.dedent("""
            🤖 **Server Status Bot**

            Welcome! This bot provides server monitoring with AI-powered status reports.

            **Available commands:**
            • /status - Get current server status
            • /plot - Generate system monitoring graphs
            • /info - Show logging status, configuration, and alerting system status
            • /top - Show top 10 CPU-consuming processes
            • /start - Start the bot and get this help message

            The status report includes:
            • System uptime
            • CPU and memory usage  
            • Disk status and SMART health
            • Temperature monitoring
            • RAID status (if available)
            • GPU status and monitoring (if available)

            Plotting shows historical data collected over time with integrated charts:
            • CPU, Memory & GPU usage combined
            • System & GPU temperatures combined  
            • Disk usage and I/O activity
        """).strip()

        welcome_message = self.rewrite(welcome_message)
        await self.send_message(update, welcome_message, markdown=1)
    
    async def plot_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /plot command - send system monitoring plots"""
        user_id = update.effective_user.id
        username = update.effective_user.username or "Unknown"
        
        logger.info(f"Plot command received from user {user_id} (@{username})")
        
        # Check if user is admin
        if not self.is_admin(user_id):
            logger.warning(f"Unauthorized access attempt from user {user_id} (@{username})")
            await update.message.reply_text(
                "🚫 Access denied. You are not authorized to use this bot."
            )
            return
        
        # Show typing indicator initially
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
        
        try:
            logger.info("Checking system monitoring data...")
            
            # Get logged data for analysis
            logged_data = self.system_monitor.get_logged_data()
            total_points = sum(len(data_list) for data_list in logged_data.values())
            
            # Check if we have sufficient data
            has_data = any(len(data_list) > 0 for data_list in logged_data.values())
            time_range = 0
            
            if has_data:
                all_timestamps = []
                for data_list in logged_data.values():
                    if data_list:
                        all_timestamps.extend([entry['timestamp'] for entry in data_list])
                
                if all_timestamps:
                    time_range = max(all_timestamps) - min(all_timestamps)
            
            # If insufficient data, send text message instead of plot
            if not has_data or time_range < 30:
                if not has_data:
                    message = "📊 **System Monitoring - No Data**\n\n"
                    message += "❌ No monitoring data available yet\n\n"
                    message += f"⏳ Wait {self.system_monitor.log_delay} seconds for data collection to begin\n"
                    message += "Then try `/plot` again"
                else:
                    log_interval = self.system_monitor.log_delay
                    message = "📊 **System Monitoring - Insufficient Data**\n\n"
                    # message += f"📈 **Data collected:** {total_points} points\n"
                    message += f"⏱️ **Time range:** {time_range:.0f} seconds\n"
                    message += f"🔄 **Log interval:** {log_interval} seconds\n\n"
                    message += f"⏳ Try again later"
                
                message = self.rewrite(message)
                await self.send_message(update, message, markdown=1)
                return
            
            # We have sufficient data, create plots
            logger.info("Generating system monitoring plots...")
            # Switch to upload photo indicator
            await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.UPLOAD_PHOTO)
            plot_buffer = self.create_system_plots()
            
            if plot_buffer is None:
                await update.message.reply_text(
                    "❌ **Error generating plots**\n\nUnexpected error occurred while creating graphs."
                )
                return
            
            # Create caption with statistics
            caption = f"📊 **System Monitoring Dashboard**\n\n"
            # caption += f"📈 **Data Points:** {total_points} total\n"
            
            # for data_type, data_list in logged_data.items():
            #     if data_list:
            #         data_name = data_type.replace('_data', '').replace('_', ' ').title()
            #         caption += f"• {data_name}: {len(data_list)} points\n"
            
            if total_points > 0:
                if time_range > 3600:  # More than 1 hour
                    range_str = f"{time_range/3600:.1f} hours"
                elif time_range > 60:  # More than 1 minute
                    range_str = f"{time_range/60:.1f} minutes"
                else:
                    range_str = f"{time_range:.0f} seconds"
                
                newest_timestamp = max(all_timestamps)
                caption += f"\n⏱️ **Time Range:** {range_str}"
                caption += f"\n🕐 **Last Update:** {datetime.fromtimestamp(newest_timestamp).strftime('%H:%M:%S')}"
            
            # Rewrite with LLM
            caption = self.rewrite(caption)
            
            # Send the plot
            await update.message.reply_photo(
                photo=plot_buffer,
                caption=caption,
                parse_mode='Markdown'
            )
            
            logger.info("System monitoring plots sent successfully")
            
        except Exception as e:
            logger.error(f"Error creating/sending plots: {e}")
            await update.message.reply_text(
                f"❌ Error generating system plots: {str(e)}\n\n"
                f"Make sure the system has been running for a while to collect data."
            )
    
    async def info_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /info command - show logging status, data collection info, and alerting status"""
        user_id = update.effective_user.id
        username = update.effective_user.username or "Unknown"
        
        logger.info(f"Info command received from user {user_id} (@{username})")
        
        # Check if user is admin
        if not self.is_admin(user_id):
            logger.warning(f"Unauthorized access attempt from user {user_id} (@{username})")
            await update.message.reply_text(
                "🚫 Access denied. You are not authorized to use this bot."
            )
            return
        
        try:
            # Get logging information
            logged_data = self.system_monitor.get_logged_data()
            total_points = sum(len(data_list) for data_list in logged_data.values())
            
            info_message = "📊 **System Monitoring Information**\n\n"
            
            # Alert system status
            info_message += f"🚨 **Alert System:**\n"
            info_message += f"• Monitoring enabled: {'✅ Yes' if self.monitoring_enabled else '❌ No'}\n"
            if self.monitoring_enabled:
                info_message += f"• Check interval: {self.monitoring_interval} seconds\n"
                info_message += f"• Notification cooldown: {self.notification_cooldown} seconds\n"
                
                # Alert types configuration
                info_message += f"\n🔍 **Alert Types:**\n"
                info_message += f"• RAID monitoring: {'✅ Yes' if self.monitor_raid else '❌ No'}\n"
                if self.monitor_cpu:
                    info_message += f"• CPU monitoring: ✅ Yes (>{self.cpu_threshold}% for {self.monitor_duration//60}min)\n"
                else:
                    info_message += f"• CPU monitoring: ❌ No\n"
                if self.monitor_temperature:
                    info_message += f"• Temperature monitoring: ✅ Yes (>{self.temperature_threshold}°C for {self.monitor_duration//60}min)\n"
                    info_message += f"  Devices: {', '.join(self.temperature_devices)}\n"
                else:
                    info_message += f"• Temperature monitoring: ❌ No\n"
                    
                if self.monitor_gpu:
                    gpu_available = "✅" if self.system_monitor.nvidia_smi_available else "❌ (no GPU/drivers)"
                    info_message += f"• GPU monitoring: {gpu_available} (temp>{self.gpu_temperature_threshold}°C, usage>{self.gpu_usage_threshold}%, memory>{self.gpu_memory_threshold}% for {self.monitor_duration//60}min)\n"
                else:
                    info_message += f"• GPU monitoring: ❌ No\n"
                
                # Last notifications status
                current_time = time.time()
                info_message += f"\n🔔 **Recent Notifications:**\n"
                if self._last_notifications:
                    for alert_type, last_sent in self._last_notifications.items():
                        time_since = current_time - last_sent
                        if time_since < 3600:  # Less than 1 hour
                            time_str = f"{time_since/60:.0f}m ago"
                        elif time_since < 86400:  # Less than 1 day
                            time_str = f"{time_since/3600:.1f}h ago"
                        else:
                            time_str = f"{time_since/86400:.1f}d ago"
                        
                        # Check if still in cooldown
                        cooldown_remaining = self.notification_cooldown - time_since
                        if cooldown_remaining > 0:
                            cooldown_str = f" (cooldown: {cooldown_remaining/60:.0f}m)"
                        else:
                            cooldown_str = ""
                        
                        alert_name = alert_type.replace('_', ' ').title()
                        info_message += f"• {alert_name}: {time_str}{cooldown_str}\n"
                else:
                    info_message += f"• No notifications sent yet\n"
                
                # Current issues status (if monitoring enabled)
                current_issues = []
                if self.monitor_cpu and self._cpu_high_start:
                    high_duration = current_time - self._cpu_high_start
                    current_issues.append(f"• High CPU detected {high_duration/60:.0f} minutes ago")
                
                if self.monitor_temperature and self._temperature_high_start:
                    high_duration = current_time - self._temperature_high_start
                    current_issues.append(f"• High temperature detected {high_duration/60:.0f} minutes ago")
                
                if self.monitor_gpu and self.system_monitor.nvidia_smi_available:
                    if self._gpu_high_temp_start:
                        high_duration = current_time - self._gpu_high_temp_start
                        current_issues.append(f"• High GPU temperature detected {high_duration/60:.0f} minutes ago")
                    
                    if self._gpu_high_usage_start:
                        high_duration = current_time - self._gpu_high_usage_start
                        current_issues.append(f"• High GPU usage detected {high_duration/60:.0f} minutes ago")
                
                if current_issues:
                    info_message += f"\n⚠️ **Current Issues:**\n"
                    info_message += "\n".join(current_issues) + "\n"
            else:
                info_message += f"• Use environment variables to enable monitoring\n"
            
            info_message += f"\n⚙️ **Data Logging:**\n"
            info_message += f"• Logging enabled: {'✅ Yes' if self.system_monitor.log_data else '❌ No'}\n"
            info_message += f"• Log interval: {self.system_monitor.log_delay} seconds\n"
            info_message += f"• Log depth: {self.system_monitor.log_depth_minutes} minutes ({self.system_monitor.log_depth_minutes/60:.1f} hours)\n"
            
            # Data collection status with detailed stats
            info_message += f"\n📈 **Data Collection Status:**\n"
            info_message += f"• Total data points: {total_points}\n"
            
            # Получаем детальную статистику
            try:
                log_stats = self.system_monitor.get_log_stats()
                for data_type, stats in log_stats.items():
                    data_name = data_type.replace('_data', '').replace('_', ' ').title()
                    if stats['count'] > 0:
                        age_hours = stats['age_minutes'] / 60
                        if age_hours >= 1:
                            age_str = f"{age_hours:.1f}h"
                        else:
                            age_str = f"{stats['age_minutes']:.0f}m"
                        info_message += f"• {data_name}: {stats['count']} points (oldest: {age_str} ago)\n"
                    else:
                        info_message += f"• {data_name}: 0 points\n"
            except Exception as e:
                # Fallback к простой статистике
                for data_type, data_list in logged_data.items():
                    data_name = data_type.replace('_data', '').replace('_', ' ').title()
                    info_message += f"• {data_name}: {len(data_list)} points\n"
            
            # Time range information
            if total_points > 0:
                all_timestamps = []
                for data_list in logged_data.values():
                    if data_list:
                        all_timestamps.extend([entry['timestamp'] for entry in data_list])
                
                if all_timestamps:
                    oldest_time = min(all_timestamps)
                    newest_time = max(all_timestamps)
                    time_range = newest_time - oldest_time
                    
                    info_message += f"\n⏱️ **Time Range:**\n"
                    info_message += f"• Oldest data: {datetime.fromtimestamp(oldest_time).strftime('%Y-%m-%d %H:%M:%S')}\n"
                    info_message += f"• Newest data: {datetime.fromtimestamp(newest_time).strftime('%Y-%m-%d %H:%M:%S')}\n"
                    
                    if time_range > 3600:  # More than 1 hour
                        range_str = f"{time_range/3600:.1f} hours"
                    elif time_range > 60:  # More than 1 minute
                        range_str = f"{time_range/60:.1f} minutes"
                    else:
                        range_str = f"{time_range:.0f} seconds"
                    
                    info_message += f"• Data span: {range_str}\n"
            
            info_message = self.rewrite(info_message)
            await self.send_message(update, info_message, markdown=1)
            
        except Exception as e:
            logger.error(f"Error getting system info: {e}")
            await update.message.reply_text(
                f"❌ Error getting system information: {str(e)}"
            )
    
    async def top_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /top command - show top 10 CPU consuming processes"""
        user_id = update.effective_user.id
        username = update.effective_user.username or "Unknown"
        
        logger.info(f"Top command received from user {user_id} (@{username})")
        
        # Check if user is admin
        if not self.is_admin(user_id):
            logger.warning(f"Unauthorized access attempt from user {user_id} (@{username})")
            await update.message.reply_text(
                "🚫 Access denied. You are not authorized to use this bot."
            )
            return
        
        # Show typing indicator
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
        
        try:
            # Get top processes data using system top command (much faster)
            logger.info("Collecting top processes...")
            top_data = self.system_monitor.get_top_processes(limit=10)
            
            if 'error' in top_data:
                error_msg = f"❌ Error getting top processes: {top_data['error']}"
                await update.message.reply_text(error_msg)
                return
            
            # Format the message
            top_message = "🔝 **Top 10 CPU-consuming processes:**\n\n"
            
            if not top_data['top_processes']:
                top_message += "No processes found."
            else:
                # Header
                top_message += "```\n"
                top_message += f"{'PID':<8} {'CPU%':<6} {'MEM%':<6} {'USER':<12} {'PROCESS'}\n"
                top_message += "-" * 60 + "\n"
                
                # Process list
                for i, proc in enumerate(top_data['top_processes'], 1):
                    pid = proc.get('pid', 'N/A')
                    cpu_percent = proc.get('cpu_percent', 0) or 0
                    memory_percent = proc.get('memory_percent', 0) or 0
                    username = proc.get('username', 'N/A')[:11]  # Truncate long usernames
                    name = proc.get('name', 'N/A')[:20]  # Truncate long process names
                    
                    top_message += f"{pid:<8} {cpu_percent:<6.1f} {memory_percent:<6.1f} {username:<12} {name}\n"
                
                top_message += "```\n"
                
                # Summary info
                timestamp = datetime.fromtimestamp(top_data['timestamp']).strftime('%H:%M:%S')
                top_message += f"\n📊 **Summary:**\n"
                top_message += f"• Total processes: {top_data['total_processes']}\n"
                top_message += f"• Processed: {top_data.get('processed_count', 'N/A')}\n"
                top_message += f"• Collection time: {top_data.get('collection_time', 'N/A')}s\n"
                top_message += f"• Data collected at: {timestamp}\n"
                
                # CPU and memory totals
                try:
                    total_cpu = sum(proc.get('cpu_percent', 0) or 0 for proc in top_data['top_processes'])
                    total_memory = sum(proc.get('memory_percent', 0) or 0 for proc in top_data['top_processes'])
                    top_message += f"• Top 10 total CPU usage: {total_cpu:.1f}%\n"
                    top_message += f"• Top 10 total memory usage: {total_memory:.1f}%\n"
                except:
                    pass
            
            # Rewrite with LLM
            processed_message = self.rewrite(top_message)
            await self.send_message(update, processed_message, markdown=1)
                
        except Exception as e:
            logger.error(f"Error getting top processes: {e}")
            await update.message.reply_text(
                f"❌ Error collecting top processes: {str(e)}"
            )
    
    async def error_handler(self, update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle errors"""
        logger.error(f"Exception while handling an update: {context.error}")
        
        if isinstance(update, Update) and update.effective_message:
            await update.effective_message.reply_text(
                "❌ An error occurred while processing your request. Please try again later."
            )
    
    async def post_init(self, application: Application) -> None:
        """Called after the application is initialized"""
        # Send startup notification to all admins
        await self.send_startup_notification(application)
        
        # Start system monitoring
        self.start_monitoring(application)
    
    def run(self):
        """Start the bot"""
        logger.info("Starting bot...")
        
        # Create application
        application = Application.builder().token(self.bot_token).post_init(self.post_init).build()
        
        # Add handlers
        application.add_handler(CommandHandler("start", self.start_command))
        application.add_handler(CommandHandler("status", self.status_command))
        application.add_handler(CommandHandler("plot", self.plot_command))
        application.add_handler(CommandHandler("info", self.info_command))
        application.add_handler(CommandHandler("top", self.top_command))
        application.add_error_handler(self.error_handler)
        
        logger.info("Bot handlers registered")
        logger.info(f"Authorized admin IDs: {list(self.admin_ids)}")
        
        # Start the bot
        try:
            application.run_polling(allowed_updates=Update.ALL_TYPES)
        except KeyboardInterrupt:
            logger.info("Bot stopped by user")
        except Exception as e:
            logger.error(f"Bot crashed: {e}")
            raise
        finally:
            # Stop monitoring when bot shuts down
            self.stop_monitoring()


def main():
    """Main entry point"""
    try:
        bot = ServerStatusBot()
        bot.run()
    except Exception as e:
        logger.error(f"Failed to start bot: {e}")
        exit(1)


if __name__ == "__main__":
    main()
