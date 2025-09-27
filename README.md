# SirCheckalot 🤖

**Telegram bot for system monitoring with LLM-powered status reports and automated alerting**

SirCheckalot is a system (Linux) monitoring bot that provides system status reports through Telegram, enhanced with AI-powered text processing for better readability and insights (that part is just for fun honestly).  
It includes alerting system that monitors RAID arrays, CPU and GPU (Nvidia) usage, and temperatures, sending notifications when issues are detected.

![Dashboard example](https://i.imgur.com/8f9dHrS.jpeg)

## ✨ Features

### 📊 System Monitoring
- **CPU & Memory**: Real-time usage, load averages, frequency information
- **Temperature Monitoring**: CPU cores, package temps, SSD temperatures, system sensors
- **Disk Health**: SMART status, disk usage, I/O statistics
- **RAID Status**: Hardware/software RAID monitoring

### 🚨 Automated Alerting System
- **RAID Monitoring**: Array failures or degradation
- **CPU Overload Detection**: CPU usage exceeds threshold for extended periods
- **Temperature Overload Detection**: Temperature exceeds threshold for extended periods

### 📈 Historical Data & Plotting
- **Time-based Data Logging**: Continuous background logging 
- **Synchronized Graphs**: CPU/Memory usage, temperatures, disk usage, I/O activity 
- **Device Filtering**: Configurable ignore patterns

### 🧠 AI-Enhanced Reports  
- **LLM Integration**: OpenAI-compatible API support (OpenAI, OpenRouter, Ollama, etc.)
- **Smart Formatting**: Text enhancement 
- **Contextual Analysis**: Interpretation of system metrics

## 🚀 Quick Start

### Prerequisites
- Python 3.8+
- Linux server with monitoring tools:
  ```bash
  sudo apt install lm-sensors smartmontools
  ```

### Installation

1. **Clone the repository**
   ```bash
   git clone https://github.com/sazonovanton/SirCheckalot.git
   cd SirCheckalot
   ```

2. **Create virtual environment**
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   ```

3. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

4. **Configure environment variables**
   ```bash
   cp env.example .env
   nano .env
   ```

5. **Run the bot**
   ```bash
   sudo -E bash -c "source venv/bin/activate && python3 main.py"
   ```

## 🚀 Running as System Service

It's recommended to run SirCheckalot as a systemd service.  

### Service Installation

1. **Copy the service file to systemd directory and edit it**

   ```bash
   sudo nano /etc/systemd/system/sircheckalot.service
   ```

   Edit the service file with correct paths:
   ```ini
   [Unit]
   Description=SirCheckalot - Telegram Server Monitoring Bot
   After=network.target
   Wants=network.target

   [Service]
   Type=simple
   User=root
   Group=root
   WorkingDirectory=/path/to/SirCheckalot
   Environment=PATH=/path/to/SirCheckalot/venv/bin:/usr/sbin:/usr/bin:/sbin:/bin
   ExecStart=/path/to/SirCheckalot/venv/bin/python /path/to/SirCheckalot/main.py
   Restart=always
   RestartSec=10
   StandardOutput=journal
   StandardError=journal
   SyslogIdentifier=sircheckalot

   # Load environment variables from .env file
   EnvironmentFile=.env

   # Security settings
   NoNewPrivileges=false
   ProtectSystem=false
   ProtectHome=false

   [Install]
   WantedBy=multi-user.target
   ```

2. **Reload systemd configuration**
   ```bash
   sudo systemctl daemon-reload
   ```

3. **Enable and start the service**
   ```bash
   sudo systemctl enable sircheckalot.service
   sudo systemctl start sircheckalot.service
   ```

## ⚙️ Configuration

### Required Environment Variables

| Variable | Description | Example |
|----------|-------------|---------|
| `TELEGRAM_BOT_TOKEN` | Bot token from @BotFather | `1234567890:ABCdefGHI...` |
| `ADMINS` | Comma-separated admin user IDs | `123456789,987654321` |
| `OPENAI_API_KEY` | API key for LLM service | `sk-or-v1-...` |

### Optional Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `OPENAI_BASE_URL` | API base URL | OpenAI default |
| `OPENAI_MODEL` | Model name | `gpt-4o-mini` |
| `OPENAI_TEMPERATURE` | Response randomness | `0.5` |
| `HTTP_PROXY` | Proxy server | None |
| `TIMEZONE` | System timezone | System default |

### System Monitoring & Alerting

Configure automated monitoring and alerting system:

| Variable | Description | Default |
|----------|-------------|---------|
| `MONITORING_ENABLED` | Enable/disable system monitoring | `true` |
| `MONITORING_INTERVAL` | Check interval in seconds | `300` (5 min) |
| `NOTIFICATION_COOLDOWN` | Cooldown between same alerts | `3600` (1 hour) |

**RAID Monitoring:**
| Variable | Description | Default |
|----------|-------------|---------|
| `MONITOR_RAID` | Enable RAID monitoring | `true` |

**CPU Monitoring:**
| Variable | Description | Default |
|----------|-------------|---------|
| `MONITOR_CPU` | Enable CPU monitoring | `true` |
| `CPU_THRESHOLD` | CPU usage threshold (%) | `80.0` |

**General Monitoring:**
| Variable | Description | Default |
|----------|-------------|---------|
| `MONITOR_DURATION` | Duration before alert (seconds) | `1200` (20 min) |

**Temperature Monitoring:**
| Variable | Description | Default |
|----------|-------------|---------|
| `MONITOR_TEMPERATURE` | Enable temperature monitoring | `true` |
| `TEMPERATURE_DEVICES` | Devices to monitor (comma-separated) | `coretemp,cpu,thermal` |
| `TEMPERATURE_THRESHOLD` | Temperature threshold (°C) | `60.0` |

### Device Filtering for Plots

Filter out unwanted devices from monitoring graphs:

| Variable | Description | Example |
|----------|-------------|---------|
| `IGNORE_PLOT_DEVICES_TEMPERATURE` | Temperature sensors to ignore | `Core,acpitz` |
| `IGNORE_PLOT_DEVICES_DISK` | Disk devices to ignore | `loop,tmpfs` |
| `IGNORE_PLOT_DEVICES_IO` | I/O devices to ignore | `loop` |

### Historical Data Logging

Configure data collection and storage for historical analysis:

| Variable | Description | Default |
|----------|-------------|---------|
| `LOG_DEPTH_MINUTES` | Data retention period in minutes | `1440` (24 hours) |
| `LOG_DELAY` | Data collection interval in seconds | `60` |

### Device Aliases

Provide friendly names for devices in graphs and status reports:

| Variable | Description | Example |
|----------|-------------|---------|
| `ALIASES_TEMPERATURE` | Temperature device aliases | `CPU::acpi_1,Motherboard::k10temp` |
| `ALIASES_DISK` | Disk device aliases | `System SSD::/dev/sda1,Data HDD::/dev/sdb1` |
| `ALIASES_IO` | I/O device aliases | `System Disk::sda,Data Disk::sdb` |

Format: `FriendlyName::TechnicalName,AnotherName::AnotherDevice`

### Temperature Device Selection

The `TEMPERATURE_DEVICES` setting uses keyword matching to filter temperature sensors. The system scans all available sensors and monitors only those containing the specified keywords.

**How it works:**
- Searches sensor names and labels for keyword matches (case-insensitive)
- Supports multiple keywords separated by commas
- Works with both hardware sensors and thermal zones

**Common device keywords:**
- `coretemp` - Intel CPU temperature sensors
- `k10temp` - AMD CPU temperature sensors  
- `cpu` - Generic CPU thermal sensors
- `thermal` - System thermal zones
- `acpi` - ACPI thermal sensors
- `nvme` - NVMe SSD temperature sensors

**Examples:**
```bash
# Intel system - monitor CPU and thermal zones
TEMPERATURE_DEVICES=coretemp,thermal

# AMD system - monitor CPU with thermal zones
TEMPERATURE_DEVICES=k10temp,cpu,thermal

# Full monitoring including SSDs
TEMPERATURE_DEVICES=coretemp,k10temp,cpu,thermal,nvme

# Critical sensors only
TEMPERATURE_DEVICES=coretemp,k10temp
```

**To discover available sensors:**
Use `/status` command to see all detected temperature sensors, then configure `TEMPERATURE_DEVICES` accordingly.

**Example configuration setups:**

**Data Logging Examples:**
```bash
# Default: 24 hours of history with 1-minute intervals
export LOG_DEPTH_MINUTES=1440
export LOG_DELAY=60

# Extended monitoring: 48 hours of history
export LOG_DEPTH_MINUTES=2880

# High-resolution monitoring: 30-second intervals, 12 hours history
export LOG_DEPTH_MINUTES=720
export LOG_DELAY=30

# Long-term monitoring: 1 week of history, 5-minute intervals
export LOG_DEPTH_MINUTES=10080
export LOG_DELAY=300
```

**Device Filtering Examples:**
```bash
# Hide individual CPU cores, keep package temperature
export IGNORE_PLOT_DEVICES_TEMPERATURE="coretemp_Core,unlabeled"

# Hide loop devices and temporary filesystems
export IGNORE_PLOT_DEVICES_DISK="loop,tmpfs,snap"

# Hide loop devices from I/O stats
export IGNORE_PLOT_DEVICES_IO="loop"
```

## 🎮 Bot Commands

| Command | Description |
|---------|-------------|
| `/start` | Show welcome message and help |
| `/status` | Get current system status report |
| `/plot` | Generate system monitoring graphs with synchronized time axes |
| `/info` | Show logging configuration, data collection statistics, and alerting system status |

## 📁 Project Structure

```
SirCheckalot/
├── main.py                 # Main bot application
├── requirements.txt        # Python dependencies
├── env.example            # Environment variables template
├── utils/
│   ├── llm_tools.py       # LLM integration module
│   ├── system_monitor.py  # System monitoring module
│   └── system_message.md  # LLM system prompt
```

## 🔧 System Requirements

### Python Packages
- `python-telegram-bot` - Telegram Bot API
- `openai` - LLM API client
- `psutil` - System monitoring
- `matplotlib` - Graph generation
- `python-dotenv` - Environment management

### System Tools
- `lm-sensors` - Hardware temperature monitoring
- `smartmontools` - Disk SMART monitoring
- `mdadm` - Software RAID monitoring (optional)

### Installation Commands
```bash
# Ubuntu/Debian
sudo apt update
sudo apt install lm-sensors smartmontools mdadm

# Initialize sensors
sudo sensors-detect --auto
```

## 🚨 Automated Alert System

### Alert Types

**🔴 RAID Alerts**
- Triggers when RAID arrays fail or become degraded
- Monitors both software (`mdadm`) and hardware RAID controllers
- Instant notification when `raid_good` status becomes `False`

**🔥 CPU Overload Alerts** 
- Monitors sustained high CPU usage
- Default: Alert when CPU > 80% for more than 20 minutes
- Prevents false alarms from temporary spikes
- Automatically resets when CPU usage returns to normal

**🌡️ Temperature Alerts**
- Configurable temperature thresholds for selected devices
- Default: Alert when monitored sensors exceed 60°C for 20+ minutes
- Shows current temperature and sensor limits
- Supports filtering by device type (CPU, thermal zones, etc.)
- Uses `MONITOR_DURATION` to prevent false alarms from temperature spikes

### Example Alert Messages

**RAID Failure:**
```
🚨 RAID ALERT

❌ Status: RAID problems detected!
🕐 Time: 2024-01-15 14:30:22

One or more RAID arrays have issues. Check server immediately!
Use /status for detailed information.
```

**High CPU Usage:**
```
🚨 HIGH CPU ALERT

⚠️ Current CPU: 85.3%
⏱️ Duration: 25 minutes  
🔥 Threshold: 80% for 20 minutes
🕐 Time: 2024-01-15 14:30:22

Server CPU usage has been high for an extended period!
Use /status for detailed information.
```

**High Temperature:**
```
🌡️ HIGH TEMPERATURE ALERT

🔥 Threshold: 60°C exceeded for 25 minutes
⏱️ Duration: 25 minutes
🕐 Time: 2024-01-15 14:30:22

High temperatures detected:
• coretemp_Package id 0: 67.2°C (high: 80, critical: 90)
• coretemp_Core 0: 65.1°C (high: 80, critical: 90)

Check server cooling system immediately!
Use /status for detailed information.
```

## 🤖 LLM Integration

The LLM integration serves one simple purpose: **to make the bot less boring!** 

Instead of dry technical output, the AI transforms system reports into readable messages with natural language. Also you can translate messages to different languages.  
Any OpenAI-compatible endpoint can be used.

**💡 Note:** The bot works perfectly fine even if LLM is unavailable - you'll just get plain output instead of the enhanced version.

## 🛠️ Development

### Running in Development
```bash
# Set development environment
export TELEGRAM_BOT_TOKEN="your_token"
export ADMINS="your_user_id"

# Run with debug logging
python3 main.py
```

### Testing Components
```bash
# Test system monitoring
python3 -m utils.system_monitor

# Test LLM integration  
python3 -m utils.llm_tools
```

### Customization
- Modify `utils/system_message.md` for different LLM behavior
- Adjust monitoring intervals in `SystemMonitor` class
- Add custom metrics in `system_monitor.py`


## 🚨 Troubleshooting

### Common Issues

**Bot doesn't respond:**
- Check `TELEGRAM_BOT_TOKEN` is valid
- Verify your user ID is in `ADMINS`
- Check bot logs for authentication errors

**No temperature data:**
- Install `lm-sensors`: `sudo apt install lm-sensors`
- Run sensor detection: `sudo sensors-detect --auto`
- Check sensor availability: `sensors`

**SMART data missing:**
- Install smartmontools: `sudo apt install smartmontools`
- Run with sudo privileges
- Check disk support: `sudo smartctl -i /dev/sda`

**LLM not working:**
- Verify `OPENAI_API_KEY` is correct
- Check API endpoint accessibility
- Review proxy settings if using `HTTP_PROXY`

**Monitoring alerts not working:**
- Check `MONITORING_ENABLED=true` in environment
- Verify admin user IDs in `ADMINS` are correct
- Review monitoring logs in bot output
- Check alert cooldown hasn't been triggered recently

**Temperature monitoring issues:**
- Ensure sensors are properly configured: `sudo sensors-detect --auto`
- Check `TEMPERATURE_DEVICES` contains correct keywords
- Use `/status` to see available temperature sensors
- Verify sensor accessibility: `sensors` command output

**RAID monitoring not working:**
- Check `/proc/mdstat` exists and is readable
- Ensure mdadm is installed: `sudo apt install mdadm`
- For hardware RAID, install vendor tools (megacli, arcconf)
- Run bot with sufficient privileges to read RAID status

### Debug Commands
```bash
# Check system sensors
sensors

# Test SMART data
sudo smartctl -a /dev/sda

# Check RAID status
cat /proc/mdstat
sudo mdadm --detail --scan

# Test temperature monitoring
cat /sys/class/thermal/thermal_zone*/temp
cat /sys/class/thermal/thermal_zone*/type

# Verify bot token
curl -s "https://api.telegram.org/bot<TOKEN>/getMe"

# Check monitoring configuration
grep MONITORING /path/to/.env
```