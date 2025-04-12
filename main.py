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
        
        required_keys = ['bot_token', 'directory', 'files', 'default_time', 'user_chat_id']
        if not config or not all(key in config for key in required_keys):
            missing = [k for k in required_keys if k not in config]
            logger.error(f"Отсутствуют ключи в конфиге: {missing}")
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

async def check_and_notify():
    tasks = await check_files(config)
    now = datetime.now()
    current_date = now.date()
    current_time = now.time().replace(second=0, microsecond=0)


    for task in tasks:
        data = task['data']
        task = data["task"]
        time = data["time"]
        predate = data["dates"].get("⏳")
        postdate = data["dates"].get("📅")
        task_time = datetime.strptime(time, '%H:%M').time().replace(second=0, microsecond=0)
        if predate:
            predate = datetime.strptime(predate, '%Y-%m-%d').date()
        if postdate:
            postdate = datetime.strptime(postdate, '%Y-%m-%d').date() 


        if task_time == current_time:
            # 1. Предварительное напоминание (⏳)
            if predate == current_date:
                message = f"⏳ Напоминаю {postdate} у Вас запланировано: \n\n {task}"
                await bot.send_message(config['user_chat_id'], message)
                logger.info(f"Предварительное напоминание: {message}")
            
            # 2. Основное напоминание (📅)
            elif postdate == current_date:
                message = f"📅 Напоминаю: \n\n {task}"
                await bot.send_message(config['user_chat_id'], message)
                logger.info(f"Основное напоминание: {message}")
            
            # 3. Основное напоминание по времени   
            elif postdate==None and predate==None:
                message = f"⏰ Напоминаю: \n\n {task}"
                await bot.send_message(config['user_chat_id'], message)
                logger.info(f"Основное напоминание: {message}")


async def scheduler():
    while True:
        await check_and_notify()
        await asyncio.sleep(60)

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