#!/usr/bin/env python3
import psutil
import subprocess
import json
import time
import re
import os
import threading
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any


class SystemMonitor:
    """Класс для мониторинга состояния сервера"""
    
    def __init__(self, ignored_partitions_prefixes: List[str] = ['/snap', '/boot'], ignored_devices_prefixes: List[str] = ['loop'], show_disk_partitions: bool = False, log_data: bool = True):
        self.sensors_available = self._check_sensors_availability()
        self.smartctl_available = self._check_smartctl_availability()
        self.nvidia_smi_available = self._check_nvidia_smi_availability()

        self.ignored_partitions_prefixes = ignored_partitions_prefixes
        self.ignored_devices_prefixes = ignored_devices_prefixes
        self.show_disk_partitions = show_disk_partitions

        self.log_data = log_data
        self.log_depth_minutes = int(os.getenv('LOG_DEPTH_MINUTES', 1440))  # 24 часа по умолчанию
        self.log_delay = int(os.getenv('LOG_DELAY', 60))
        self.temperature_data = [] # [{"label": "CPU", "temperature": 50, "timestamp": 1717334400}, ...]
        self.cpu_usage_data = [] # [{"usage": 50, "timestamp": 1717334400}, ...]
        self.memory_usage_data = [] # [{"usage": 50, "timestamp": 1717334400}, ...]
        self.disk_usage_data = [] # [{"device": "sda", "usage": 50, "timestamp": 1717334400}, ...]
        self.disk_io_data = [] # [{"device": "sda", "read_count": 1000000, "write_count": 1000000, "timestamp": 1717334400}, ...]
        self.gpu_data = [] # [{"gpu_id": 0, "temperature": 60, "gpu_usage": 80, "memory_usage": 90, "memory_used": 8000, "memory_total": 12000, "timestamp": 1717334400}, ...]
        self.raid_good = None # True if RAID is good, False if RAID is bad, None if RAID is not checked
        
        # Переменные для управления фоновым логированием
        self._logging_thread = None
        self._stop_logging = threading.Event()
        self._data_lock = threading.Lock()  # Для thread-safe доступа к данным
        
        # Автоматический запуск логирования при создании объекта
        if self.log_data:
            self.start_logging()
            
        # Если GPU недоступен, добавляем однократную запись об этом
        if self.log_data and not self.nvidia_smi_available:
            self._add_initial_gpu_unavailable_record()
        
        if not self.sensors_available:
            print("Sensors are not available, try to install lm-sensors with `sudo apt install lm-sensors`")
        if not self.smartctl_available:
            print("Smartctl is not available, try to install smartmontools with `sudo apt install smartmontools`")
        if not self.nvidia_smi_available:
            print("nvidia-smi is not available, try to install nvidia drivers or check if GPU is present")
    
    def start_logging(self):
        """Запускает фоновое логирование системных метрик"""
        if self._logging_thread is None or not self._logging_thread.is_alive():
            self._stop_logging.clear()
            self._logging_thread = threading.Thread(target=self._logging_worker, daemon=True)
            self._logging_thread.start()
            print(f"🟢 System logging started (interval: {self.log_delay}s, depth: {self.log_depth_minutes} minutes)")
    
    def stop_logging(self):
        """Останавливает фоновое логирование"""
        if self._logging_thread and self._logging_thread.is_alive():
            self._stop_logging.set()
            self._logging_thread.join(timeout=5)  # Ждем до 5 секунд завершения потока
            print("🔴 System logging stopped")
    
    def _logging_worker(self):
        """Основной метод фонового потока для сбора данных"""
        while not self._stop_logging.is_set():
            try:
                self._collect_metrics()
            except Exception as e:
                print(f"⚠️  Logging error: {e}")
            
            # Ждем указанное время или пока не поступит сигнал остановки
            self._stop_logging.wait(self.log_delay)
    
    def _add_to_log(self, log_list: List, data: Dict):
        """Добавляет данные в лог"""
        with self._data_lock:
            log_list.append(data)
    
    def _cleanup_old_entries(self, log_list: List):
        """Удаляет записи старше log_depth_minutes минут"""
        if not log_list:
            return
            
        current_time = int(time.time())
        max_age_seconds = self.log_depth_minutes * 60
        cutoff_time = current_time - max_age_seconds
        
        # Удаляем записи старше cutoff_time
        # Используем список comprehension для создания нового списка без старых записей
        filtered_entries = [entry for entry in log_list if entry['timestamp'] >= cutoff_time]
        
        # Очищаем исходный список и добавляем отфильтрованные записи
        log_list.clear()
        log_list.extend(filtered_entries)
    
    def _add_initial_gpu_unavailable_record(self):
        """Добавляет однократную запись о недоступности GPU в исторические данные"""
        timestamp = int(time.time())
        self._add_to_log(self.gpu_data, {
            'gpu_id': 'N/A',
            'temperature': None,
            'gpu_usage': None,
            'memory_usage': None,
            'memory_used': None,
            'memory_total': None,
            'power_draw': None,
            'power_limit': None,
            'status': 'nvidia-smi_unavailable',  # Специальный флаг для отличия от обычных записей
            'timestamp': timestamp
        })
    
    def _collect_metrics(self):
        """Собирает все метрики системы и добавляет их в соответствующие логи"""
        timestamp = int(time.time())
        
        # Периодическая очистка старых записей в начале каждого цикла
        with self._data_lock:
            self._cleanup_old_entries(self.temperature_data)
            self._cleanup_old_entries(self.cpu_usage_data)
            self._cleanup_old_entries(self.memory_usage_data)
            self._cleanup_old_entries(self.disk_usage_data)
            self._cleanup_old_entries(self.disk_io_data)
            self._cleanup_old_entries(self.gpu_data)
        
        # Флаги для отслеживания успешного сбора данных
        temperature_collected = False
        cpu_memory_collected = False  
        disk_collected = False
        
        # 1. Собираем температуры
        try:
            temp_data = self.get_temperatures()
            
            # Обрабатываем psutil температуры
            for sensor_name, sensor_list in temp_data.items():
                if sensor_name.endswith('_error') or sensor_name in ['lm_sensors', 'thermal_zones']:
                    continue
                if isinstance(sensor_list, list):
                    for sensor in sensor_list:
                        if 'current' in sensor:
                            self._add_to_log(self.temperature_data, {
                                'label': f"{sensor_name}_{sensor.get('label', 'unlabeled')}",
                                'temperature': sensor['current'],
                                'timestamp': timestamp
                            })
                            temperature_collected = True
            
            # Обрабатываем thermal zones
            if 'thermal_zones' in temp_data and temp_data['thermal_zones']:
                for zone in temp_data['thermal_zones']:
                    self._add_to_log(self.temperature_data, {
                        'label': zone['type'],
                        'temperature': zone['temperature'],
                        'timestamp': timestamp
                    })
                    temperature_collected = True
                    
        except Exception as e:
            print(f"⚠️  Temperature logging error: {e}")
        
        # Если температуры недоступны, добавляем запись об отсутствии данных
        if not temperature_collected:
            self._add_to_log(self.temperature_data, {
                'label': 'no_data',
                'temperature': None,
                'timestamp': timestamp
            })
        
        # 2. Собираем данные CPU и памяти (критически важные - должны быть всегда)
        try:
            load_data = self.get_cpu_memory_load()
            if 'cpu' in load_data:
                cpu_usage = load_data['cpu'].get('percent_total', 0)
                self._add_to_log(self.cpu_usage_data, {
                    'usage': cpu_usage,
                    'timestamp': timestamp
                })
                cpu_memory_collected = True
            
            if 'memory' in load_data:
                memory_usage = load_data['memory'].get('percent', 0)
                self._add_to_log(self.memory_usage_data, {
                    'usage': memory_usage,
                    'timestamp': timestamp
                })
                
        except Exception as e:
            print(f"⚠️  CPU/Memory logging error: {e}")
            # CPU/Memory критичны - добавляем нулевые значения при ошибке
            if not cpu_memory_collected:
                self._add_to_log(self.cpu_usage_data, {
                    'usage': 0,
                    'timestamp': timestamp
                })
                self._add_to_log(self.memory_usage_data, {
                    'usage': 0,
                    'timestamp': timestamp
                })
        
        # 3. Собираем данные дисков
        try:
            disk_data = self.get_disk_status()
            
            # Использование дисков
            if 'usage' in disk_data:
                for mount, info in disk_data['usage'].items():
                    self._add_to_log(self.disk_usage_data, {
                        'device': info['device'],
                        'mount': mount,
                        'usage': info['percent'],
                        'timestamp': timestamp
                    })
                    disk_collected = True
            
            # I/O статистика
            if 'io_stats' in disk_data:
                for device, stats in disk_data['io_stats'].items():
                    self._add_to_log(self.disk_io_data, {
                        'device': device,
                        'read_count': stats['read_count'],
                        'write_count': stats['write_count'],
                        'read_bytes': stats['read_bytes'],
                        'write_bytes': stats['write_bytes'],
                        'timestamp': timestamp
                    })
                    
        except Exception as e:
            print(f"⚠️  Disk logging error: {e}")
        
        # Если дисковые данные недоступны, добавляем записи об отсутствии данных
        if not disk_collected:
            self._add_to_log(self.disk_usage_data, {
                'device': 'no_data',
                'mount': '/no_data',
                'usage': None,
                'timestamp': timestamp
            })
            self._add_to_log(self.disk_io_data, {
                'device': 'no_data',
                'read_count': 0,
                'write_count': 0,
                'read_bytes': 0,
                'write_bytes': 0,
                'timestamp': timestamp
            })
        
        # 4. Проверяем статус RAID
        try:
            self._update_raid_status()
        except Exception as e:
            print(f"⚠️  RAID status logging error: {e}")
        
        # 5. Собираем данные GPU
        try:
            if self.nvidia_smi_available:
                gpu_data = self.get_gpu_status()
                if 'gpus' in gpu_data and gpu_data['gpus']:
                    for gpu in gpu_data['gpus']:
                        # Вычисляем процент использования памяти как процент заполнения
                        memory_usage_percent = None
                        if gpu.get('memory_used') is not None and gpu.get('memory_total') is not None:
                            memory_total = gpu.get('memory_total')
                            if memory_total > 0:
                                memory_usage_percent = (gpu.get('memory_used') / memory_total) * 100
                        
                        self._add_to_log(self.gpu_data, {
                            'gpu_id': gpu['id'],
                            'temperature': gpu.get('temperature'),
                            'gpu_usage': gpu.get('utilization_gpu'),
                            'memory_usage': memory_usage_percent,  # Теперь используем процент заполнения вместо утилизации
                            'memory_used': gpu.get('memory_used'),
                            'memory_total': gpu.get('memory_total'),
                            'memory_utilization': gpu.get('utilization_memory'),  # Сохраняем утилизацию отдельно
                            'power_draw': gpu.get('power_draw'),
                            'power_limit': gpu.get('power_limit'),
                            'timestamp': timestamp
                        })
                else:
                    # Если GPU данные недоступны, добавляем запись об отсутствии данных
                    self._add_to_log(self.gpu_data, {
                        'gpu_id': 0,
                        'temperature': None,
                        'gpu_usage': None,
                        'memory_usage': None,
                        'memory_used': None,
                        'memory_total': None,
                        'power_draw': None,
                        'power_limit': None,
                        'timestamp': timestamp
                    })
        except Exception as e:
            print(f"⚠️  GPU logging error: {e}")
            # Добавляем запись об ошибке
            self._add_to_log(self.gpu_data, {
                'gpu_id': 0,
                'temperature': None,
                'gpu_usage': None,
                'memory_usage': None,
                'memory_used': None,
                'memory_total': None,
                'power_draw': None,
                'power_limit': None,
                'timestamp': timestamp
            })
    
    def _update_raid_status(self):
        """Обновляет общий статус RAID массивов"""
        try:
            raid_data = self.get_raid_status()
            
            # Проверяем software RAID массивы
            if 'arrays' in raid_data and raid_data['arrays']:
                all_healthy = True
                has_arrays = False
                
                for array in raid_data['arrays']:
                    has_arrays = True
                    # Проверяем здоровье массива
                    if 'raid_info' in array:
                        if not array['raid_info'].get('healthy', False):
                            all_healthy = False
                            break
                    else:
                        # Если нет информации о здоровье, считаем как проблему
                        all_healthy = False
                        break
                
                if has_arrays:
                    self.raid_good = all_healthy
                else:
                    self.raid_good = None
            
            # Проверяем hardware RAID (пока просто отмечаем что найден)
            elif 'hardware_raid' in raid_data and raid_data['hardware_raid']:
                # Для hardware RAID пока что просто отмечаем как найденный
                # В будущем можно добавить более детальную проверку
                self.raid_good = True  # Предполагаем что работает, если найден
            
            else:
                # RAID массивы не найдены
                self.raid_good = None
                
        except Exception as e:
            # В случае ошибки проверки оставляем статус неопределенным
            self.raid_good = None
            raise e
    
    def get_logged_data(self) -> Dict[str, List]:
        """Возвращает накопленные данные логов"""
        with self._data_lock:
            return {
                'temperature_data': self.temperature_data.copy(),
                'cpu_usage_data': self.cpu_usage_data.copy(),
                'memory_usage_data': self.memory_usage_data.copy(),
                'disk_usage_data': self.disk_usage_data.copy(),
                'disk_io_data': self.disk_io_data.copy(),
                'gpu_data': self.gpu_data.copy()
            }
    
    def get_log_stats(self) -> Dict[str, Any]:
        """Возвращает статистику по логам"""
        with self._data_lock:
            current_time = int(time.time())
            stats = {}
            
            for log_name, log_data in {
                'temperature_data': self.temperature_data,
                'cpu_usage_data': self.cpu_usage_data,
                'memory_usage_data': self.memory_usage_data,
                'disk_usage_data': self.disk_usage_data,
                'disk_io_data': self.disk_io_data,
                'gpu_data': self.gpu_data
            }.items():
                if log_data:
                    oldest_timestamp = min(entry['timestamp'] for entry in log_data)
                    newest_timestamp = max(entry['timestamp'] for entry in log_data)
                    age_minutes = (current_time - oldest_timestamp) / 60
                    stats[log_name] = {
                        'count': len(log_data),
                        'age_minutes': age_minutes,
                        'oldest': datetime.fromtimestamp(oldest_timestamp).isoformat(),
                        'newest': datetime.fromtimestamp(newest_timestamp).isoformat()
                    }
                else:
                    stats[log_name] = {
                        'count': 0,
                        'age_minutes': 0,
                        'oldest': None,
                        'newest': None
                    }
            
            return stats
    
    def clear_logs(self):
        """Очищает все накопленные логи"""
        with self._data_lock:
            self.temperature_data.clear()
            self.cpu_usage_data.clear()
            self.memory_usage_data.clear()
            self.disk_usage_data.clear()
            self.disk_io_data.clear()
            self.gpu_data.clear()
        print("🗑️  All logs cleared")
    
    def __del__(self):
        """Деструктор - останавливает логирование при удалении объекта"""
        try:
            self.stop_logging()
        except:
            pass
    
    def _check_sensors_availability(self) -> bool:
        """Проверяет доступность lm-sensors"""
        try:
            subprocess.run(['sensors'], capture_output=True, check=True, timeout=5)
            return True
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
            return False
    
    def _check_smartctl_availability(self) -> bool:
        """Проверяет доступность smartctl"""
        try:
            subprocess.run(['smartctl', '--version'], capture_output=True, check=True, timeout=5)
            return True
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
            return False
    
    def _check_nvidia_smi_availability(self) -> bool:
        """Проверяет доступность nvidia-smi"""
        try:
            subprocess.run(['nvidia-smi', '-L'], capture_output=True, check=True, timeout=5)
            return True
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
            return False
    
    def _is_partition(self, device_name: str) -> bool:
        """Определяет, является ли устройство разделом"""
        # RAID массивы (md0, md1) не считаются разделами
        if device_name.startswith('md'):
            return False
            
        # Обычные диски: sda1, sdb2, etc.
        if re.match(r'^[a-z]+\d+$', device_name):
            return True
            
        # NVMe диски: nvme0n1p1, nvme0n1p2, etc.
        if re.match(r'^nvme\d+n\d+p\d+$', device_name):
            return True
            
        # MMC/SD карты: mmcblk0p1, etc.
        if re.match(r'^mmcblk\d+p\d+$', device_name):
            return True
            
        return False
    
    def get_uptime(self) -> Dict[str, Any]:
        """
        Возвращает время работы системы
        
        Returns:
            Dict с информацией о времени работы системы
        """
        try:
            boot_time = psutil.boot_time()
            uptime_seconds = time.time() - boot_time
            
            uptime_td = timedelta(seconds=uptime_seconds)
            days = uptime_td.days
            hours, remainder = divmod(uptime_td.seconds, 3600)
            minutes, seconds = divmod(remainder, 60)
            
            boot_datetime = datetime.fromtimestamp(boot_time)
            
            return {
                'uptime_seconds': int(uptime_seconds),
                'uptime_formatted': f"{days}d {hours}h {minutes}m {seconds}s",
                'boot_time': boot_datetime.isoformat(),
                'days': days,
                'hours': hours,
                'minutes': minutes,
                'seconds': seconds
            }
        except Exception as e:
            return {'error': f"Failed to get uptime: {str(e)}"}
    
    def get_temperatures(self) -> Dict[str, Any]:
        """
        Возвращает температуры системы
        
        Returns:
            Dict с температурами различных компонентов
        """
        temperatures = {}
        
        # Попытка получить температуры через psutil
        try:
            if hasattr(psutil, 'sensors_temperatures'):
                psutil_temps = psutil.sensors_temperatures()
                for sensor_name, sensor_list in psutil_temps.items():
                    temperatures[sensor_name] = []
                    for sensor in sensor_list:
                        temperatures[sensor_name].append({
                            'label': sensor.label or 'unlabeled',
                            'current': sensor.current,
                            'high': sensor.high,
                            'critical': sensor.critical
                        })
        except Exception as e:
            temperatures['psutil_error'] = str(e)
        
        # Попытка получить температуры через lm-sensors
        if self.sensors_available:
            try:
                result = subprocess.run(['sensors', '-A', '-j'], 
                                      capture_output=True, text=True, timeout=10)
                if result.returncode == 0:
                    sensors_data = json.loads(result.stdout)
                    temperatures['lm_sensors'] = sensors_data
            except Exception as e:
                temperatures['lm_sensors_error'] = str(e)
        
        # Чтение из /sys/class/thermal/ как fallback
        try:
            thermal_zones = []
            thermal_path = '/sys/class/thermal/'
            if os.path.exists(thermal_path):
                for zone_dir in os.listdir(thermal_path):
                    if zone_dir.startswith('thermal_zone'):
                        zone_path = os.path.join(thermal_path, zone_dir)
                        temp_file = os.path.join(zone_path, 'temp')
                        type_file = os.path.join(zone_path, 'type')
                        
                        if os.path.exists(temp_file):
                            with open(temp_file, 'r') as f:
                                temp_millidegrees = int(f.read().strip())
                                temp_celsius = temp_millidegrees / 1000.0
                            
                            zone_type = 'unknown'
                            if os.path.exists(type_file):
                                with open(type_file, 'r') as f:
                                    zone_type = f.read().strip()
                            
                            thermal_zones.append({
                                'zone': zone_dir,
                                'type': zone_type,
                                'temperature': temp_celsius
                            })
            
            if thermal_zones:
                temperatures['thermal_zones'] = thermal_zones
                
        except Exception as e:
            temperatures['thermal_zones_error'] = str(e)
        
        return temperatures
    
    def get_raid_status(self) -> Dict[str, Any]:
        """
        Возвращает состояние RAID массивов
        
        Returns:
            Dict с информацией о состоянии RAID
        """
        raid_info = {}
        
        # Проверка /proc/mdstat для software RAID
        try:
            if os.path.exists('/proc/mdstat'):
                with open('/proc/mdstat', 'r') as f:
                    mdstat_content = f.read()
                
                raid_info['mdstat_raw'] = mdstat_content
                
                # Парсинг mdstat
                arrays = []
                lines = mdstat_content.split('\n')
                current_array = None
                
                for line in lines:
                    if line.startswith('md'):
                        # Новый массив
                        parts = line.split()
                        if len(parts) >= 4:
                            # Извлекаем устройства из строки (все части после типа RAID)
                            devices = []
                            for part in parts[4:]:
                                # Убираем квадратные скобки с номерами: sdd1[1] -> sdd1
                                device_name = re.sub(r'\[\d+\]', '', part)
                                if device_name and not device_name.startswith('['):
                                    devices.append({
                                        'name': device_name,
                                        'raw': part  # Оригинальное представление с номером
                                    })
                            
                            current_array = {
                                'device': parts[0],
                                'status': parts[2],
                                'type': parts[3],
                                'devices': devices
                            }
                            arrays.append(current_array)
                    
                    elif current_array:
                        # Парсинг дополнительной информации о массиве
                        line_stripped = line.strip()
                        
                        # Строка с размером и статусом: "3906884608 blocks super 1.2 [2/2] [UU]"
                        if 'blocks' in line_stripped:
                            parts = line_stripped.split()
                            for i, part in enumerate(parts):
                                if part == 'blocks':
                                    # Размер в блоках
                                    if i > 0 and parts[i-1].isdigit():
                                        current_array['blocks'] = int(parts[i-1])
                                    
                                    # Версия super блока
                                    if i+2 < len(parts) and parts[i+1] == 'super':
                                        current_array['super_version'] = parts[i+2]
                                    break
                            
                            # Парсинг статуса массива [2/2] [UU]
                            raid_status_match = re.search(r'\[(\d+)/(\d+)\]\s*\[([U_]+)\]', line_stripped)
                            if raid_status_match:
                                total_devices = int(raid_status_match.group(1))
                                active_devices = int(raid_status_match.group(2))
                                device_status = raid_status_match.group(3)
                                
                                current_array['raid_info'] = {
                                    'total_devices': total_devices,
                                    'active_devices': active_devices,
                                    'device_status': device_status,
                                    'healthy': '_' not in device_status
                                }
                        
                        # Bitmap информация
                        elif 'bitmap:' in line_stripped:
                            current_array['bitmap'] = line_stripped
                        
                        # Строка с прогрессом операций
                        elif any(keyword in line_stripped for keyword in ['recovery', 'resync', 'rebuild', 'check']):
                            progress_match = re.search(r'(\d+\.\d+)%', line_stripped)
                            if progress_match:
                                current_array['progress'] = float(progress_match.group(1))
                                
                            # Парсинг типа операции
                            for operation in ['recovery', 'resync', 'rebuild', 'check']:
                                if operation in line_stripped:
                                    current_array['operation'] = operation
                                    break
                            
                            # Время до завершения
                            eta_match = re.search(r'finish=(\d+\.\d+)min', line_stripped)
                            if eta_match:
                                current_array['eta_minutes'] = float(eta_match.group(1))
                            
                            # Скорость
                            speed_match = re.search(r'speed=(\d+)K/sec', line_stripped)
                            if speed_match:
                                current_array['speed_kb_sec'] = int(speed_match.group(1))
                
                raid_info['arrays'] = arrays
        except Exception as e:
            raid_info['mdstat_error'] = str(e)
        
        # Проверка hardware RAID через различные утилиты
        hardware_raid = {}
        
        # LSI/Broadcom MegaRAID
        try:
            result = subprocess.run(['megacli', '-AdpAllInfo', '-aALL'], 
                                  capture_output=True, text=True, timeout=15)
            if result.returncode == 0:
                hardware_raid['megacli'] = result.stdout
        except Exception:
            pass
        
        # Adaptec RAID
        try:
            result = subprocess.run(['arcconf', 'getconfig', '1'], 
                                  capture_output=True, text=True, timeout=15)
            if result.returncode == 0:
                hardware_raid['adaptec'] = result.stdout
        except Exception:
            pass
        
        if hardware_raid:
            raid_info['hardware_raid'] = hardware_raid
        
        return raid_info
    
    def get_disk_status(self) -> Dict[str, Any]:
        """
        Возвращает состояние дисков (health, usage)
        
        Returns:
            Dict с информацией о состоянии дисков
        """
        disk_info = {}
        
        # Информация об использовании дисков через psutil
        try:
            disk_usage = {}
            partitions = psutil.disk_partitions()
            
            for partition in partitions:
                if any(partition.mountpoint.startswith(prefix) for prefix in self.ignored_partitions_prefixes):
                    continue
                try:
                    usage = psutil.disk_usage(partition.mountpoint)
                    disk_usage[partition.mountpoint] = {
                        'device': partition.device,
                        'fstype': partition.fstype,
                        'total': usage.total,
                        'used': usage.used,
                        'free': usage.free,
                        'percent': usage.percent
                    }
                except PermissionError:
                    # Пропускаем недоступные разделы
                    continue
            
            disk_info['usage'] = disk_usage
        except Exception as e:
            disk_info['usage_error'] = str(e)
        
        # IO статистика
        try:
            disk_io = psutil.disk_io_counters(perdisk=True)
            disk_info['io_stats'] = {}
            
            for device, stats in disk_io.items():
                if any(device.startswith(prefix) for prefix in self.ignored_devices_prefixes):
                    continue
                
                # Пропускаем разделы, если show_disk_partitions = False
                if not self.show_disk_partitions and self._is_partition(device):
                    continue
                    
                disk_info['io_stats'][device] = {
                    'read_count': stats.read_count,
                    'write_count': stats.write_count,
                    'read_bytes': stats.read_bytes,
                    'write_bytes': stats.write_bytes,
                    'read_time': stats.read_time,
                    'write_time': stats.write_time
                }
        except Exception as e:
            disk_info['io_stats_error'] = str(e)
        
        # SMART информация через smartctl
        if self.smartctl_available:
            try:
                smart_info = {}
                
                # Получаем список дисков
                result = subprocess.run(['lsblk', '-d', '-n', '-o', 'NAME,TYPE'], 
                                      capture_output=True, text=True, timeout=10)
                
                if result.returncode == 0:
                    for line in result.stdout.strip().split('\n'):
                        parts = line.split()
                        if len(parts) >= 2 and parts[1] == 'disk':
                            device = f"/dev/{parts[0]}"
                            
                            try:
                                smart_result = subprocess.run(
                                    ['smartctl', '-H', '-i', device], 
                                    capture_output=True, text=True, timeout=10
                                )
                                
                                if smart_result.returncode in [0, 4]:  # 0 = OK, 4 = некоторые тесты не прошли
                                    health_status = 'UNKNOWN'
                                    model = 'Unknown'
                                    
                                    for line in smart_result.stdout.split('\n'):
                                        if 'SMART overall-health' in line:
                                            if 'PASSED' in line:
                                                health_status = 'PASSED'
                                            elif 'FAILED' in line:
                                                health_status = 'FAILED'
                                        elif 'Device Model:' in line:
                                            model = line.split(':', 1)[1].strip()
                                    
                                    smart_info[device] = {
                                        'health': health_status,
                                        'model': model,
                                        'raw_output': smart_result.stdout
                                    }
                            except Exception as e:
                                smart_info[device] = {'error': str(e)}
                
                disk_info['smart'] = smart_info
                
            except Exception as e:
                disk_info['smart_error'] = str(e)
        
        return disk_info
    
    def get_cpu_memory_load(self) -> Dict[str, Any]:
        """
        Возвращает загрузку CPU и памяти
        
        Returns:
            Dict с информацией о загрузке CPU и памяти
        """
        try:
            load_info = {}
            
            # CPU информация
            cpu_percent = psutil.cpu_percent(percpu=True)
            cpu_count_logical = psutil.cpu_count(logical=True)
            cpu_count_physical = psutil.cpu_count(logical=False)
            cpu_freq = psutil.cpu_freq()
            
            load_info['cpu'] = {
                'percent_per_core': cpu_percent,
                'percent_total': sum(cpu_percent) / len(cpu_percent),
                'count_logical': cpu_count_logical,
                'count_physical': cpu_count_physical,
                'frequency': {
                    'current': cpu_freq.current if cpu_freq else None,
                    'min': cpu_freq.min if cpu_freq else None,
                    'max': cpu_freq.max if cpu_freq else None
                }
            }
            
            # Load average
            try:
                load_avg = os.getloadavg()
                load_info['load_average'] = {
                    '1min': load_avg[0],
                    '5min': load_avg[1],
                    '15min': load_avg[2]
                }
            except Exception:
                pass
            
            # Память
            memory = psutil.virtual_memory()
            swap = psutil.swap_memory()
            
            load_info['memory'] = {
                'total': memory.total,
                'available': memory.available,
                'used': memory.used,
                'free': memory.free,
                'percent': memory.percent,
                'buffers': memory.buffers if hasattr(memory, 'buffers') else None,
                'cached': memory.cached if hasattr(memory, 'cached') else None
            }
            
            load_info['swap'] = {
                'total': swap.total,
                'used': swap.used,
                'free': swap.free,
                'percent': swap.percent
            }
            
            return load_info
            
        except Exception as e:
            return {'error': f"Failed to get CPU/Memory load: {str(e)}"}
    
    def get_top_processes(self, limit: int = 10) -> Dict[str, Any]:
        """
        Возвращает топ процессов по использованию CPU используя команду top
        
        Args:
            limit: Количество процессов для вывода (по умолчанию 10)
            
        Returns:
            Dict с информацией о топ процессах
        """
        try:
            start_time = time.time()
            
            # Используем команду top для получения списка процессов
            # -b: batch mode (non-interactive)
            # -n1: only one iteration 
            # -o %CPU: sort by CPU usage
            # -w512: wide output to avoid truncation
            cmd = ['top', '-b', '-n1', '-o', '%CPU', '-w', '512']
            
            try:
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=5, encoding='utf-8', errors='ignore')
                if result.returncode != 0:
                    return {'error': f"top command failed: {result.stderr}"}
                
                output = result.stdout
                
            except subprocess.TimeoutExpired:
                return {'error': "top command timed out (5 seconds)"}
            except FileNotFoundError:
                return {'error': "top command not found on system"}
            
            # Парсим вывод top
            processes = []
            lines = output.split('\n')
            
            # Ищем начало списка процессов (строка с заголовками)
            process_start_idx = None
            for i, line in enumerate(lines):
                if 'PID' in line and 'USER' in line and '%CPU' in line and '%MEM' in line and ('COMMAND' in line or 'КОМАНДА' in line):
                    process_start_idx = i + 1
                    break
            
            if process_start_idx is None:
                return {'error': "Could not find process list in top output"}
            
            # Парсим процессы
            total_processes = 0
            for line in lines[process_start_idx:]:
                # Убираем управляющие символы и лишние пробелы
                line = re.sub(r'[^\x20-\x7E]', '', line).strip()
                if not line:
                    continue
                    
                try:
                    # Разбиваем строку на части (top использует пробелы как разделители)
                    parts = line.split()
                    if len(parts) < 10:  # Минимум полей для корректной строки процесса
                        continue
                    
                    # Проверяем, что первый элемент - это PID (число)
                    pid = int(parts[0])
                    
                    # Извлекаем данные процесса
                    user = parts[1]
                    
                    # Ищем позиции %CPU и %MEM
                    cpu_percent = None
                    mem_percent = None
                    command_start_idx = None
                    
                    for i, part in enumerate(parts):
                        # %CPU обычно содержит запятую или точку
                        if (',' in part or '.' in part) and cpu_percent is None:
                            try:
                                cpu_val = float(part.replace(',', '.'))
                                if 0 <= cpu_val <= 1000:  # Разумный диапазон для CPU%
                                    cpu_percent = cpu_val
                                    # %MEM обычно следует за %CPU
                                    if i + 1 < len(parts):
                                        try:
                                            mem_val = float(parts[i + 1].replace(',', '.'))
                                            if 0 <= mem_val <= 100:  # Разумный диапазон для MEM%
                                                mem_percent = mem_val
                                                command_start_idx = i + 3  # Команда обычно через несколько полей после %MEM
                                                break
                                        except ValueError:
                                            pass
                            except ValueError:
                                continue
                    
                    if cpu_percent is None or mem_percent is None or command_start_idx is None:
                        continue
                    
                    # COMMAND может содержать пробелы, поэтому берем все оставшиеся части
                    if command_start_idx < len(parts):
                        command = ' '.join(parts[command_start_idx:])
                    else:
                        command = parts[-1] if parts else 'unknown'
                    
                    # Ограничиваем длину команды для читаемости
                    if len(command) > 30:
                        command = command[:27] + '...'
                    
                    process_info = {
                        'pid': pid,
                        'name': command,
                        'cpu_percent': cpu_percent,
                        'memory_percent': mem_percent,
                        'username': user
                    }
                    
                    processes.append(process_info)
                    total_processes += 1
                    
                    # Останавливаемся когда набрали нужное количество
                    if len(processes) >= limit:
                        break
                        
                except (ValueError, IndexError):
                    # Пропускаем строки, которые не удается распарсить
                    continue
            
            collection_time = time.time() - start_time
            
            # Получаем общее количество процессов из заголовка top
            total_tasks = None
            for line in lines[:10]:  # Ищем в первых 10 строках
                if 'Tasks:' in line or 'Total:' in line or 'Задачи:' in line:
                    try:
                        # Пример: "Tasks: 284 total" или "Задачи: 782 total"
                        parts = line.split()
                        for i, part in enumerate(parts):
                            if part.replace(',', '').isdigit() and i > 0:
                                if i+1 < len(parts) and ('total' in parts[i+1] or 'всего' in parts[i+1]):
                                    total_tasks = int(part.replace(',', ''))
                                    break
                    except (ValueError, IndexError):
                        pass
                    break
            
            return {
                'top_processes': processes,
                'total_processes': total_tasks or total_processes,
                'processed_count': len(processes),
                'collection_time': round(collection_time, 2),
                'timestamp': time.time()
            }
            
        except Exception as e:
            return {'error': f"Failed to get top processes: {str(e)}"}
    
    def get_gpu_status(self) -> Dict[str, Any]:
        """
        Возвращает состояние GPU Nvidia
        
        Returns:
            Dict с информацией о GPU Nvidia
        """
        gpu_info = {}
        
        if not self.nvidia_smi_available:
            gpu_info['error'] = "nvidia-smi not available"
            return gpu_info
        
        try:
            # Получаем данные в XML формате для более надежного парсинга
            result = subprocess.run(['nvidia-smi', '-q', '-x'], 
                                  capture_output=True, text=True, timeout=10)
            
            if result.returncode != 0:
                gpu_info['error'] = f"nvidia-smi error: {result.stderr}"
                return gpu_info
            
            # Парсим XML
            import xml.etree.ElementTree as ET
            root = ET.fromstring(result.stdout)
            
            gpus = []
            gpu_elements = root.findall('.//gpu')
            
            for i, gpu_elem in enumerate(gpu_elements):
                gpu_data = {
                    'id': i,
                    'name': None,
                    'temperature': None,
                    'utilization_gpu': None,
                    'utilization_memory': None,
                    'memory_used': None,
                    'memory_total': None,
                    'memory_free': None,
                    'power_draw': None,
                    'power_limit': None,
                    'fan_speed': None,
                    'driver_version': None
                }
                
                # Название GPU
                product_name = gpu_elem.find('.//product_name')
                if product_name is not None:
                    gpu_data['name'] = product_name.text
                
                # Температура
                temp_elem = gpu_elem.find('.//temperature/gpu_temp')
                if temp_elem is not None:
                    try:
                        temp_text = temp_elem.text
                        if temp_text and temp_text != 'N/A':
                            gpu_data['temperature'] = float(temp_text.replace(' C', ''))
                    except (ValueError, AttributeError):
                        pass
                
                # Утилизация GPU
                util_gpu = gpu_elem.find('.//utilization/gpu_util')
                if util_gpu is not None:
                    try:
                        util_text = util_gpu.text
                        if util_text and util_text != 'N/A':
                            gpu_data['utilization_gpu'] = float(util_text.replace(' %', ''))
                    except (ValueError, AttributeError):
                        pass
                
                # Утилизация памяти
                util_memory = gpu_elem.find('.//utilization/memory_util')
                if util_memory is not None:
                    try:
                        util_text = util_memory.text
                        if util_text and util_text != 'N/A':
                            gpu_data['utilization_memory'] = float(util_text.replace(' %', ''))
                    except (ValueError, AttributeError):
                        pass
                
                # Информация о памяти
                memory_total = gpu_elem.find('.//fb_memory_usage/total')
                memory_used = gpu_elem.find('.//fb_memory_usage/used')
                memory_free = gpu_elem.find('.//fb_memory_usage/free')
                
                if memory_total is not None:
                    try:
                        mem_text = memory_total.text
                        if mem_text and mem_text != 'N/A':
                            gpu_data['memory_total'] = float(mem_text.replace(' MiB', ''))
                    except (ValueError, AttributeError):
                        pass
                
                if memory_used is not None:
                    try:
                        mem_text = memory_used.text
                        if mem_text and mem_text != 'N/A':
                            gpu_data['memory_used'] = float(mem_text.replace(' MiB', ''))
                    except (ValueError, AttributeError):
                        pass
                
                if memory_free is not None:
                    try:
                        mem_text = memory_free.text
                        if mem_text and mem_text != 'N/A':
                            gpu_data['memory_free'] = float(mem_text.replace(' MiB', ''))
                    except (ValueError, AttributeError):
                        pass
                
                # Потребляемая мощность
                power_draw = gpu_elem.find('.//power_readings/power_draw')
                if power_draw is not None:
                    try:
                        power_text = power_draw.text
                        if power_text and power_text != 'N/A':
                            gpu_data['power_draw'] = float(power_text.replace(' W', ''))
                    except (ValueError, AttributeError):
                        pass
                
                # Лимит мощности
                power_limit = gpu_elem.find('.//power_readings/power_limit')
                if power_limit is not None:
                    try:
                        power_text = power_limit.text
                        if power_text and power_text != 'N/A':
                            gpu_data['power_limit'] = float(power_text.replace(' W', ''))
                    except (ValueError, AttributeError):
                        pass
                
                # Скорость вентилятора
                fan_speed = gpu_elem.find('.//fan_speed')
                if fan_speed is not None:
                    try:
                        fan_text = fan_speed.text
                        if fan_text and fan_text != 'N/A':
                            gpu_data['fan_speed'] = float(fan_text.replace(' %', ''))
                    except (ValueError, AttributeError):
                        pass
                
                gpus.append(gpu_data)
            
            gpu_info['gpus'] = gpus
            
            # Версия драйвера
            driver_version = root.find('.//driver_version')
            if driver_version is not None:
                gpu_info['driver_version'] = driver_version.text
            
            # Версия CUDA
            cuda_version = root.find('.//cuda_version')
            if cuda_version is not None:
                gpu_info['cuda_version'] = cuda_version.text
            
        except Exception as e:
            gpu_info['error'] = f"Failed to get GPU status: {str(e)}"
        
        return gpu_info
    
    def get_full_system_status(self) -> Dict[str, Any]:
        """
        Возвращает полную информацию о состоянии системы
        
        Returns:
            Dict со всей информацией о системе
        """
        return {
            'timestamp': datetime.now().isoformat(),
            'uptime': self.get_uptime(),
            'temperatures': self.get_temperatures(),
            'raid_status': self.get_raid_status(),
            'disk_status': self.get_disk_status(),
            'cpu_memory_load': self.get_cpu_memory_load(),
            'gpu_status': self.get_gpu_status()
        }
    
    def get_human_readable_status(self) -> str:
        """
        Возвращает состояние системы в человекочитаемом виде
        
        Returns:
            Строка с форматированной информацией о системе
        """
        output = []
        output.append("=" * 50)
        output.append("🖥️  SYSTEM MONITOR REPORT")
        output.append("=" * 50)
        output.append("")
        
        # 1. UPTIME
        try:
            uptime_data = self.get_uptime()
            if 'error' in uptime_data:
                output.append("⏱️  UPTIME: Error - " + uptime_data['error'])
            else:
                output.append(f"⏱️  UPTIME: {uptime_data['uptime_formatted']}")
                output.append(f"   Boot time: {uptime_data['boot_time']}")
        except Exception as e:
            output.append(f"⏱️  UPTIME: Error - {str(e)}")
        output.append("")
        
        # 2. CPU & MEMORY
        try:
            load_data = self.get_cpu_memory_load()
            if 'error' in load_data:
                output.append("🔥 CPU & MEMORY: Error - " + load_data['error'])
            else:
                cpu = load_data.get('cpu', {})
                memory = load_data.get('memory', {})
                swap = load_data.get('swap', {})
                load_avg = load_data.get('load_average', {})
                
                output.append("🔥 CPU & MEMORY:")
                output.append(f"   CPU Usage: {cpu.get('percent_total', 0):.1f}% ({cpu.get('count_physical', 0)} cores / {cpu.get('count_logical', 0)} threads)")
                
                if cpu.get('frequency'):
                    freq = cpu['frequency']
                    current_ghz = freq.get('current', 0) / 1000 if freq.get('current') else 0
                    max_ghz = freq.get('max', 0) / 1000 if freq.get('max') else 0
                    output.append(f"   CPU Frequency: {current_ghz:.2f}GHz (max: {max_ghz:.1f}GHz)")
                
                if load_avg:
                    output.append(f"   Load Average: {load_avg.get('1min', 0):.2f} (1min), {load_avg.get('5min', 0):.2f} (5min), {load_avg.get('15min', 0):.2f} (15min)")
                
                if memory:
                    total_gb = memory.get('total', 0) / (1024**3)
                    used_gb = memory.get('used', 0) / (1024**3)
                    available_gb = memory.get('available', 0) / (1024**3)
                    output.append(f"   Memory: {memory.get('percent', 0):.1f}% used ({used_gb:.1f}GB / {total_gb:.1f}GB, {available_gb:.1f}GB available)")
                
                if swap:
                    swap_total_gb = swap.get('total', 0) / (1024**3)
                    swap_used_gb = swap.get('used', 0) / (1024**3)
                    if swap_total_gb > 0:
                        output.append(f"   Swap: {swap.get('percent', 0):.1f}% used ({swap_used_gb:.1f}GB / {swap_total_gb:.1f}GB)")
        except Exception as e:
            output.append(f"🔥 CPU & MEMORY: Error - {str(e)}")
        output.append("")
        
        # 3. DISK STATUS
        try:
            disk_data = self.get_disk_status()
            output.append("💾 DISK STATUS:")
            
            # Disk Usage
            if 'usage' in disk_data and disk_data['usage']:
                output.append("   📁 Disk Usage:")
                for mount, info in disk_data['usage'].items():
                    total_hr = info['total'] 
                    if total_hr > 1024 ** 4:
                        total_hr = f"{total_hr / (1024 ** 4):.1f}TB"
                    elif total_hr > 1024 ** 3:
                        total_hr = f"{total_hr / (1024 ** 3):.1f}GB"
                    else:
                        total_hr = f"{total_hr / (1024 ** 2):.1f}MB"
                    used_hr = info['used'] 
                    if used_hr > 1024 ** 4:
                        used_hr = f"{used_hr / (1024 ** 4):.1f}TB"
                    elif used_hr > 1024 ** 3:
                        used_hr = f"{used_hr / (1024 ** 3):.1f}GB"
                    else:
                        used_hr = f"{used_hr / (1024 ** 2):.1f}MB"
                    free_hr = info['free'] 
                    if free_hr > 1024 ** 4:
                        free_hr = f"{free_hr / (1024 ** 4):.1f}TB"
                    elif free_hr > 1024 ** 3:
                        free_hr = f"{free_hr / (1024 ** 3):.1f}GB"
                    else:
                        free_hr = f"{free_hr / (1024 ** 2):.1f}MB"
                    output.append(f"     {mount}: {info['percent']:.1f}% used ({used_hr} / {total_hr}, {free_hr} free) - Device: {info['device']}, FS: {info['fstype']}")
            
            # SMART Status
            if 'smart' in disk_data and disk_data['smart']:
                output.append("   🔍 SMART Status:")
                for device, smart_info in disk_data['smart'].items():
                    if 'error' in smart_info:
                        output.append(f"     {device}: Error - {smart_info['error']}")
                    else:
                        status_emoji = "✅" if smart_info['health'] == 'PASSED' else "❌"
                        
                        # Парсим дополнительную информацию из raw_output
                        rotation_rate = None
                        form_factor = None
                        if 'raw_output' in smart_info:
                            for line in smart_info['raw_output'].split('\n'):
                                if 'Rotation Rate:' in line:
                                    rotation_rate = line.split(':', 1)[1].strip()
                                elif 'Form Factor:' in line:
                                    form_factor = line.split(':', 1)[1].strip()
                        
                        # Основная информация
                        output.append(f"     {device}: {status_emoji} {smart_info['health']} ({smart_info['model']})")
                        
                        # Дополнительная информация
                        if rotation_rate or form_factor:
                            additional_info = []
                            if rotation_rate:
                                additional_info.append(f"Rotation: {rotation_rate}")
                            if form_factor:
                                additional_info.append(f"Form Factor: {form_factor}")
                            output.append(f"       {', '.join(additional_info)}")
            elif 'smart_error' in disk_data:
                output.append(f"   🔍 SMART Status: Error - {disk_data['smart_error']}")
            else:
                output.append("   🔍 SMART Status: Not available (install smartmontools with `sudo apt install smartmontools`)")
            
            # I/O Statistics (top 3)
            if 'io_stats' in disk_data and disk_data['io_stats']:
                output.append("   📊 I/O Statistics:")
                io_stats = disk_data['io_stats']
                sorted_disks = sorted(io_stats.items(), 
                                    key=lambda x: x[1]['read_count'] + x[1]['write_count'], 
                                    reverse=True)
                
                for device, stats in sorted_disks:
                    read_hr = stats['read_bytes'] / (1024**2) # human readable
                    if read_hr > 1024:
                        read_hr = f"{read_hr / 1024:.1f}GB"
                    elif read_hr > 1024 * 1024:
                        read_hr = f"{read_hr / (1024 * 1024):.1f}TB"
                    else:
                        read_hr = f"{read_hr:.1f}MB"
                    write_hr = stats['write_bytes'] / (1024**2) # human readable
                    if write_hr > 1024:
                        write_hr = f"{write_hr / 1024:.1f}GB"
                    elif write_hr > 1024 * 1024:
                        write_hr = f"{write_hr / (1024 * 1024):.1f}TB"
                    else:
                        write_hr = f"{write_hr:.1f}MB"

                    read_count_hr = stats['read_count'] # human readable (i.e. 1000000 -> 1M)
                    if read_count_hr > 10**6:
                        read_count_hr = f"{read_count_hr / 10**6:.1f}M"
                    elif read_count_hr > 10**3:
                        read_count_hr = f"{read_count_hr / 10**3:.1f}K"
                    else:
                        read_count_hr = f"{read_count_hr:.1f}"
                    write_count_hr = stats['write_count'] # human readable (i.e. 1000000 -> 1M)
                    if write_count_hr > 10**6:
                        write_count_hr = f"{write_count_hr / 10**6:.1f}M"
                    elif write_count_hr > 10**3:
                        write_count_hr = f"{write_count_hr / 10**3:.1f}K"
                    else:
                        write_count_hr = f"{write_count_hr:.1f}"
                    output.append(f"     {device}: {read_count_hr} reads ({read_hr}), {write_count_hr} writes ({write_hr})")
                        
        except Exception as e:
            output.append(f"💾 DISK STATUS: Error - {str(e)}")
        output.append("")
        
        # 4. TEMPERATURES
        try:
            temp_data = self.get_temperatures()
            output.append("🌡️  TEMPERATURES:")
            
            temp_found = False
            
            # Hardware Sensors (psutil)
            for sensor_name, sensor_list in temp_data.items():
                if sensor_name.endswith('_error') or sensor_name in ['lm_sensors', 'thermal_zones']:
                    continue
                if isinstance(sensor_list, list) and sensor_list:
                    if not temp_found:
                        output.append("   🔧 Hardware Sensors:")
                        temp_found = True
                    output.append(f"     {sensor_name}:")
                    for sensor in sensor_list:
                        label = sensor['label'] if sensor['label'] != 'unlabeled' else sensor_name
                        current = sensor['current']
                        high = sensor.get('high')
                        critical = sensor.get('critical')
                        
                        temp_status = "🟢"
                        if critical and current >= critical:
                            temp_status = "🔴"
                        elif high and current >= high:
                            temp_status = "🟡"
                        
                        limits = ""
                        if high or critical:
                            limits = f" (high: {high if high else 'N/A'}, critical: {critical if critical else 'N/A'})"
                        
                        output.append(f"       {temp_status} {label}: {current:.1f}°C{limits}")
            
            # Thermal Zones
            if 'thermal_zones' in temp_data and temp_data['thermal_zones']:
                output.append("   🌡️  Thermal Zones:")
                for zone in temp_data['thermal_zones']:
                    temp_status = "🟢" if zone['temperature'] < 70 else "🟡" if zone['temperature'] < 85 else "🔴"
                    output.append(f"     {temp_status} {zone['type']}: {zone['temperature']:.1f}°C")
            
            # Errors
            for key, value in temp_data.items():
                if key.endswith('_error'):
                    output.append(f"   ❌ {key}: {value}")
                    
        except Exception as e:
            output.append(f"🌡️  TEMPERATURES: Error - {str(e)}")
        output.append("")
        
        # 5. GPU STATUS
        try:
            gpu_data = self.get_gpu_status()
            output.append("🎮 GPU STATUS:")
            
            if 'error' in gpu_data:
                output.append(f"   ❌ Error: {gpu_data['error']}")
            elif 'gpus' in gpu_data and gpu_data['gpus']:
                for gpu in gpu_data['gpus']:
                    gpu_name = gpu.get('name', f"GPU {gpu['id']}")
                    output.append(f"   🖥️  {gpu_name}:")
                    
                    # Температура
                    temp = gpu.get('temperature')
                    if temp is not None:
                        temp_status = "🟢" if temp < 70 else "🟡" if temp < 85 else "🔴"
                        output.append(f"     {temp_status} Temperature: {temp:.1f}°C")
                    
                    # Загрузка GPU
                    gpu_util = gpu.get('utilization_gpu')
                    if gpu_util is not None:
                        util_status = "🟢" if gpu_util < 70 else "🟡" if gpu_util < 90 else "🔴"
                        output.append(f"     {util_status} GPU Usage: {gpu_util:.1f}%")
                    
                    # Использование памяти
                    memory_used = gpu.get('memory_used')
                    memory_total = gpu.get('memory_total')
                    memory_util = gpu.get('utilization_memory')
                    
                    if memory_used is not None and memory_total is not None:
                        memory_used_gb = memory_used / 1024
                        memory_total_gb = memory_total / 1024
                        memory_percent = (memory_used / memory_total) * 100 if memory_total > 0 else 0
                        
                        memory_status = "🟢" if memory_percent < 70 else "🟡" if memory_percent < 90 else "🔴"
                        output.append(f"     {memory_status} Memory: {memory_percent:.1f}% used ({memory_used_gb:.1f}GB / {memory_total_gb:.1f}GB)")
                        
                        if memory_util is not None:
                            output.append(f"       Memory Utilization: {memory_util:.1f}%")
                    
                    # Потребляемая мощность
                    power_draw = gpu.get('power_draw')
                    power_limit = gpu.get('power_limit')
                    
                    if power_draw is not None:
                        power_info = f"Power: {power_draw:.1f}W"
                        if power_limit is not None:
                            power_percent = (power_draw / power_limit) * 100 if power_limit > 0 else 0
                            power_status = "🟢" if power_percent < 70 else "🟡" if power_percent < 90 else "🔴"
                            power_info += f" / {power_limit:.1f}W ({power_percent:.1f}%)"
                        else:
                            power_status = "🟢"
                        output.append(f"     {power_status} {power_info}")
                    
                    # Скорость вентилятора
                    fan_speed = gpu.get('fan_speed')
                    if fan_speed is not None:
                        fan_status = "🟢" if fan_speed < 70 else "🟡" if fan_speed < 90 else "🔴"
                        output.append(f"     {fan_status} Fan Speed: {fan_speed:.1f}%")
                    
                    output.append("")
                
                # Версии драйверов
                if 'driver_version' in gpu_data:
                    output.append(f"   📚 Driver Version: {gpu_data['driver_version']}")
                if 'cuda_version' in gpu_data:
                    output.append(f"   📚 CUDA Version: {gpu_data['cuda_version']}")
            else:
                output.append("   ℹ️  No NVIDIA GPUs detected")
                    
        except Exception as e:
            output.append(f"🎮 GPU STATUS: Error - {str(e)}")
        output.append("")
        
        # 6. RAID STATUS
        try:
            raid_data = self.get_raid_status()
            output.append("🔗 RAID STATUS:")
            
            # Software RAID
            if 'arrays' in raid_data and raid_data['arrays']:
                output.append("   💿 Software RAID Arrays:")
                for array in raid_data['arrays']:
                    output.append(f"     🔸 {array['device']}: {array['status']} ({array['type']})")
                    
                    # Устройства
                    if 'devices' in array and array['devices']:
                        device_names = [dev['name'] for dev in array['devices']]
                        output.append(f"       Devices: {', '.join(device_names)}")
                    
                    # Размер
                    if 'blocks' in array:
                        size_gb = array['blocks'] * 1024 / (1024**3)
                        output.append(f"       Size: {size_gb:.1f}GB")
                    
                    # Здоровье RAID
                    if 'raid_info' in array:
                        info = array['raid_info']
                        health_emoji = "✅" if info['healthy'] else "❌"
                        output.append(f"       Health: {health_emoji} [{info['device_status']}] ({info['active_devices']}/{info['total_devices']} devices active)")
                    
                    # Операции (rebuild, resync, etc.)
                    if 'operation' in array:
                        op_info = f"       Operation: {array['operation'].upper()}"
                        if 'progress' in array:
                            op_info += f" {array['progress']:.1f}%"
                        if 'eta_minutes' in array:
                            op_info += f" (ETA: {array['eta_minutes']:.1f}min)"
                        if 'speed_kb_sec' in array:
                            speed_mb_sec = array['speed_kb_sec'] / 1024
                            if speed_mb_sec >= 1024:
                                speed_gb_sec = speed_mb_sec / 1024
                                op_info += f" at {speed_gb_sec:.1f}GB/s"
                            else:
                                op_info += f" at {speed_mb_sec:.1f}MB/s"
                        output.append(op_info)
                    
                    output.append("")
            
            # Hardware RAID
            if 'hardware_raid' in raid_data and raid_data['hardware_raid']:
                output.append("   🖥️  Hardware RAID:")
                hw_raid = raid_data['hardware_raid']
                if 'megacli' in hw_raid:
                    output.append("     🔸 MegaRAID controller detected")
                if 'adaptec' in hw_raid:
                    output.append("     🔸 Adaptec RAID controller detected")
            
            # Errors
            if 'mdstat_error' in raid_data:
                output.append(f"   ❌ MDSTAT Error: {raid_data['mdstat_error']}")
            
            if not any(['arrays' in raid_data and raid_data['arrays'], 
                       'hardware_raid' in raid_data and raid_data['hardware_raid']]):
                output.append("   ℹ️  No RAID arrays detected")
                        
        except Exception as e:
            output.append(f"🔗 RAID STATUS: Error - {str(e)}")
        
        return "\n".join(output)


if __name__ == "__main__":
    # Test
    # 0. Create virtual environment with `python3 -m venv venv`
    # 1. Activate virtual environment with `source venv/bin/activate`
    # 2. Install dependencies with `pip install psutil`
    # 3. Run the script with `sudo -E python3 -m utils.system_monitor` (sudo needed to run some commands)
    
    print("=== System Monitor ===")
    monitor = SystemMonitor(log_data=True)  # Включаем логирование
    print(monitor.get_human_readable_status())
    
    # Демонстрация работы с логами
    try:
        print("\n=== Logging Demo ===")
        print("Logging is now running in background...")
        print("Waiting 10 seconds to collect some data...")
        time.sleep(10)
        
        # Получаем накопленные данные
        logged_data = monitor.get_logged_data()
        
        print(f"\n📊 Collected data:")
        for data_type, data_list in logged_data.items():
            print(f"  {data_type}: {len(data_list)} entries")
            if data_list:
                # Показываем последние 3 записи
                for entry in data_list[-3:]:
                    timestamp_str = datetime.fromtimestamp(entry['timestamp']).strftime('%H:%M:%S')
                    if 'temperature' in entry:
                        print(f"    [{timestamp_str}] {entry['label']}: {entry['temperature']:.1f}°C")
                    elif 'usage' in entry:
                        print(f"    [{timestamp_str}] Usage: {entry['usage']:.1f}%")
                    elif 'device' in entry and 'read_count' in entry:
                        print(f"    [{timestamp_str}] {entry['device']}: {entry['read_count']} reads, {entry['write_count']} writes")
                    elif 'device' in entry and 'mount' in entry:
                        print(f"    [{timestamp_str}] {entry['device']} ({entry['mount']}): {entry['usage']:.1f}%")
                    elif 'gpu_id' in entry:
                        # Проверяем статус недоступности GPU
                        if entry.get('status') == 'nvidia-smi_unavailable':
                            gpu_info = "GPU: nvidia-smi unavailable (no GPU or drivers)"
                        else:
                            gpu_info = f"GPU{entry['gpu_id']}"
                            if entry.get('temperature') is not None:
                                gpu_info += f": {entry['temperature']:.1f}°C"
                            if entry.get('gpu_usage') is not None:
                                gpu_info += f", {entry['gpu_usage']:.1f}% GPU"
                            if entry.get('memory_usage') is not None:
                                gpu_info += f", {entry['memory_usage']:.1f}% Memory"
                            if entry.get('power_draw') is not None:
                                gpu_info += f", {entry['power_draw']:.1f}W"
                        print(f"    [{timestamp_str}] {gpu_info}")
        
    except KeyboardInterrupt:
        print("\n\nStopping...")
    finally:
        monitor.stop_logging()
