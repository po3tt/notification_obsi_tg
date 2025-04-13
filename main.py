import re
import yaml
from pathlib import Path
from datetime import datetime, time, timedelta 
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.client.default import DefaultBotProperties
import asyncio
import logging



logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

CONFIG_FILE = 'config.yaml'

def load_config():
    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)
        
        required_keys = ['bot_token', 'directory', 'files', 'default_time', 'user_chat_id', 'check_interval']
        if not config or not all(key in config for key in required_keys):
            missing = [k for k in required_keys if k not in config]
            logger.error(f"Отсутствуют ключи в конфиге: {missing}")
            return None
            
        # Проверка корректности интервала
        if not isinstance(config['check_interval'], int) or config['check_interval'] < 1:
            logger.error("check_interval должен быть целым числом (секунды) и не меньше 1")
            return None
            
        return config
    except Exception as e:
        logger.error(f"Ошибка конфига: {str(e)}")
        return None

config = load_config()
if not config:
    exit(1)

bot = Bot(token=config['bot_token'], default=DefaultBotProperties(parse_mode='HTML'))
dp = Dispatcher()

def parse_task_line(line, default_time):
    line = line.strip().split('#')[0].strip()
    
    if not re.match(r'^- \[ \]', line):
        return None
    
    content = re.sub(r'^- \[ \]\s*', '', line)
    
    time_start = None
    elements = {}
    task_text = []

    parts = re.split(r'(\s*[⏰⏳📅]\s*)', content)
    for i, part in enumerate(parts):
        if re.match(r'^\s*[⏰⏳📅]\s*$', part):
            emoji = part.strip()
            if i+1 < len(parts):
                value = parts[i+1].split()[0] if parts[i+1] else None
                if value and re.match(r'^[\d:-]+$', value):
                    elements[emoji] = value
                    parts[i+1] = parts[i+1].replace(value, '', 1).strip()
        else:
            task_text.append(part.strip())

    if task_text:
        first_part = task_text[0]
        time_match = re.match(r'^(\d{1,2}:\d{2})(\s+|$)', first_part)
        if time_match:
            time_start = time_match.group(1)
            task_text[0] = first_part.replace(time_match.group(0), '', 1).strip()

    task_clean = ' '.join(filter(None, task_text))
    time_clock = elements.get('⏰')
    
    def is_valid_time(t):
        return re.fullmatch(r'\d{1,2}:\d{2}', t) is not None
    
    def is_valid_date(d):
        try:
            datetime.strptime(d, '%Y-%m-%d')
            return True
        except:
            return False
    
    final_time = next(
        (t for t in [time_clock, time_start, default_time] 
        if t and is_valid_time(t)),
        default_time
    )
    
    valid_dates = {}
    for emoji in ['⏳', '📅']:
        value = elements.get(emoji)
        if value and is_valid_date(value):
            valid_dates[emoji] = value

    return {
        'task': task_clean,
        'time': final_time,
        'dates': valid_dates
    } if task_clean else None

async def check_files(config):
    results = []
    directory = Path(config['directory'])
    
    for filename in config['files']:
        filepath = directory / filename
        if not filepath.exists():
            logger.warning(f"Файл не найден: {filepath}")
            continue
            
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                for line_num, line in enumerate(f, 1):
                    task_data = parse_task_line(line, config['default_time'])
                    if task_data:
                        results.append({
                            'file': filename,
                            'line': line_num,
                            'data': task_data
                        })
        except Exception as e:
            logger.error(f"Ошибка чтения файла: {str(e)}")
    
    return results


async def process_tasks_for_time(check_time: datetime):
    """Обработка задач для конкретного времени проверки"""
    tasks = await check_files(config)
    current_date = check_time.date()
    current_time = check_time.time().replace(second=0, microsecond=0)
    
    for task in tasks:
        data = task['data']
        task_text = data["task"]
        time_str = data["time"]
        predate = data["dates"].get("⏳")
        postdate = data["dates"].get("📅")

        try:
            task_time = datetime.strptime(time_str, '%H:%M').time()
            task_time = task_time.replace(second=0, microsecond=0)
        except ValueError:
            continue

        # Преобразование дат
        predate_obj = datetime.strptime(predate, '%Y-%m-%d').date() if predate else None
        postdate_obj = datetime.strptime(postdate, '%Y-%m-%d').date() if postdate else None

        # Проверка условий для конкретного времени
        if task_time == current_time:
            if predate_obj == current_date:
                message = f"⏳ [Восстановлено] Напоминаю {postdate} у Вас запланировано: \n\n {task_text}"
                await bot.send_message(config['user_chat_id'], message)
            
            if postdate_obj == current_date:
                message = f"📅 [Восстановлено] Напоминаю: \n\n {task_text}"
                await bot.send_message(config['user_chat_id'], message)
            
            if not predate and not postdate:
                message = f"⏰ [Восстановлено] Напоминаю: \n\n {task_text}"
                await bot.send_message(config['user_chat_id'], message)

async def check_and_notify():
    """Основная функция проверки с восстановлением пропущенных интервалов"""
    now = datetime.now()
    
    # Инициализация времени последней проверки
    if not hasattr(check_and_notify, 'last_check_time'):
        check_and_notify.last_check_time = now - timedelta(seconds=config['check_interval'])
        logger.info(f"Инициализация last_check_time: {check_and_notify.last_check_time}")
    
    # Расчет пропущенных интервалов
    time_diff = (now - check_and_notify.last_check_time).total_seconds()
    
    if time_diff > config['check_interval'] * 1.5:
        missed_checks = int(time_diff // config['check_interval'])
        logger.warning(f"Пропущено проверок: {missed_checks}, восстановление...")
        
        # Обработка пропущенных периодов
        for i in range(1, missed_checks + 1):
            check_time = check_and_notify.last_check_time + timedelta(
                seconds=config['check_interval'] * i
            )
            logger.debug(f"Проверка за {check_time}")
            await process_tasks_for_time(check_time)
    
    # Обработка текущих задач
    logger.info(f"Обычная проверка в {now}")
    await process_tasks_for_time(now)
    
    # Обновление времени последней проверки
    check_and_notify.last_check_time = now

async def scheduler():
    """Модифицированный планировщик с обработкой ошибок"""
    while True:
        try:
            await check_and_notify()
            await asyncio.sleep(config['check_interval'])
        except Exception as e:
            logger.error(f"Ошибка в планировщике: {str(e)}")
            await asyncio.sleep(10)  # Пауза перед повторной попыткой



@dp.message(Command("start"))
async def send_welcome(message: types.Message):
    await message.reply(
        "🔔 Бот для напоминаний запущен!\n"
        "Уведомления будут приходить в настроенный чат.\n"
        "Используйте /check для просмотра задач"
    )

@dp.message(Command("check"))
async def check_tasks(message: types.Message):
    tasks = await check_files(config)
    
    if not tasks:
        await message.reply("❌ Активных задач не найдено")
        return
    
    response = ["📋 Список задач:\n"]
    for task in tasks:
        task_info = task['data']
        dates = '\n'.join([f"{emoji} {date}" for emoji, date in task_info['dates'].items()]) or ""
        
        response.append(
            f" {task_info['task']} в {task_info['time']} {dates}\n"
            f"-------"
        )
    
    await message.reply('\n'.join(response))

async def main():
    asyncio.create_task(scheduler())
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())
