import re
import yaml
from pathlib import Path
from datetime import datetime, time, timedelta 
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.client.default import DefaultBotProperties
import asyncio
import logging
from aiogram.enums import ParseMode


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

CONFIG_FILE = 'config.yaml'

def load_config():
    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)
        
        required_keys = ['bot_token', 'directory', 'default_time', 'user_chat_id', 'check_interval']
        missing = [k for k in required_keys if k not in config]
        if missing:
            logger.error(f"Отсутствуют ключи в конфиге: {missing}")
            return None

        if not isinstance(config['check_interval'], int) or config['check_interval'] < 1:
            logger.error("check_interval должен быть целым числом (секунды) и не меньше 1")
            return None

        config.setdefault('files', []) 
        config.setdefault('show_path_in_message', True) 

        return config
    except Exception as e:
        logger.error(f"Ошибка конфига: {str(e)}")
        return None


config = load_config()
if not config:
    exit(1)

bot = Bot(
    token=config['bot_token'], 
    default=DefaultBotProperties(parse_mode=ParseMode.HTML))

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
    files = config.get('files', [])

    if not files:  
        files_to_scan = list(directory.rglob("*.md")) 
    else:
        files_to_scan = [directory / f for f in files]

    for filepath in files_to_scan:
        if not filepath.exists():
            logger.warning(f"Файл не найден: {filepath}")
            continue

        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                for line_num, line in enumerate(f, 1):
                    task_data = parse_task_line(line, config['default_time'])
                    if task_data:
                        results.append({
                            'file': filepath, 
                            'line': line_num,
                            'data': task_data
                        })
        except Exception as e:
            logger.error(f"Ошибка чтения файла {filepath}: {str(e)}")

    return results

async def process_tasks_for_time(check_time: datetime):
    tasks = await check_files(config)
    current_date = check_time.date()
    current_time = check_time.time().replace(second=0, microsecond=0)
    
    for task in tasks:
        try:
            data = task['data']
            task_text = data["task"]
            time_str = data["time"]
            predate = data["dates"].get("⏳")
            postdate = data["dates"].get("📅")
            
            task_time = datetime.strptime(time_str, '%H:%M').time()
            task_time = task_time.replace(second=0, microsecond=0)

            if task_time == current_time:
                message = None
                
                if predate and datetime.strptime(predate, '%Y-%m-%d').date() == current_date:
                    message = f"⏳ Напоминаю {postdate} у вас запланировано:\n\n - {task_text}"
                
                elif postdate and datetime.strptime(postdate, '%Y-%m-%d').date() == current_date:
                    message = f"📅 Напоминание:\n\n - {task_text}"
                
                elif not predate and not postdate:
                    message = f"⏰ Напоминание:\n\n - {task_text}"
                
                if message:
                    if config.get('show_path_in_message', True):
                        filename = task['file'].relative_to(config['directory'])
                        safe_filename = str(filename).replace(".md", "") 
                        message += f"\n📁 Файл: {safe_filename}"
                                            
                    await bot.send_message(
                        chat_id=config['user_chat_id'],
                        text=message,
                        parse_mode=ParseMode.HTML,
                        disable_web_page_preview=True
                    )
                    logger.info(f"Отправлено сообщение: {message}")

        except Exception as e:
            logger.error(f"Ошибка обработки задачи: {str(e)}")


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
    """Планировщик с синхронизацией по времени (например, каждую минуту в xx:xx:01)"""
    while True:
        try:
            now = datetime.now()
            next_check = (
                now.replace(second=1, microsecond=0) + 
                timedelta(minutes=1) if now.second >= 1 else
                now.replace(second=1, microsecond=0)
            )

            await check_and_notify()
            
            sleep_duration = (next_check - datetime.now()).total_seconds()
            logger.info(f"Следующая проверка через {sleep_duration:.2f} секунд (в {next_check.time()})")
            await asyncio.sleep(sleep_duration)

        except Exception as e:
            logger.error(f"Ошибка в планировщике: {str(e)}")
            await asyncio.sleep(10)



@dp.message(Command("start"))
async def send_welcome(message: types.Message):
    keyboard = types.ReplyKeyboardMarkup(
        keyboard=[
            [types.KeyboardButton(text="🔔Задачи на сегодня")]
        ],
        resize_keyboard=True,
        one_time_keyboard=True
    )
    
    await message.reply(
        "🔔 Бот для напоминаний запущен!\n"
        "Уведомления будут приходить автоматически.\n"
        "Нажмите кнопку ниже для просмотра задач на сегодня",
        reply_markup=keyboard
    )

@dp.message(Command("scheduled"))
@dp.message(lambda message: message.text == "🔔Задачи на сегодня")
async def show_scheduled_tasks(message: types.Message):
    tasks = await check_files(config)
    today = datetime.now().date()
    keyboard = types.ReplyKeyboardMarkup(
        keyboard=[
            [types.KeyboardButton(text="🔔Задачи на сегодня")]
        ],
        resize_keyboard=True,
        one_time_keyboard=True
    )
    response = ["📅 <b>Задачи на сегодня:</b>\n"]
    
    for task in tasks:
        data = task['data']
        dates = data['dates']
        time_str = data['time']
        
        # Проверяем совпадение дат
        is_today = False
        if '⏳' in dates:
            remind_date = datetime.strptime(dates['⏳'], '%Y-%m-%d').date()
            if remind_date == today:
                is_today = True
        if '📅' in dates:
            event_date = datetime.strptime(dates['📅'], '%Y-%m-%d').date()
            if event_date == today:
                is_today = True
        if not dates:
            try:
                task_time = datetime.strptime(time_str, '%H:%M').time()
                if task_time >= datetime.now().time():
                    is_today = True
            except:
                pass
        
        if is_today:
            file_info = ""
            if config.get('show_path_in_message', True):
                filename = task['file'].relative_to(config['directory'])
                safe_filename = str(filename).replace(".md", "") 
                file_info += f"\n📁 Файл: {safe_filename} \n"

            task_info = (
                f"⏰ {time_str} - {data['task']}\n"
                f"{file_info}"
                "━━━━━━━━━━━━━━━━━━━━━━"
            )
            response.append(task_info)
    
    if len(response) == 1:
        response.append("\n✅ На сегодня задач нет!")
    
    await message.reply(
        '\n'.join(response),
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard
    )

async def main():
    asyncio.create_task(scheduler())
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())
