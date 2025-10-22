import disnake
from disnake.ext import commands, tasks
from disnake import TextInputStyle
import json
import os
from datetime import datetime
import re
import random
import string
import psycopg2
from psycopg2.extras import RealDictCursor
import urllib.parse
import asyncio

intents = disnake.Intents.default()
intents.members = True
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

# Конфигурация для разных серверов
SERVER_CONFIGS = {
    1429544000188317831: {
        "static_channel_id": 1429831404379705474,
        "admin_role_ids": [1310673963000528949, 1223589384452833290, 1429544345463296000],
    },
    1003525677640851496: {
        "static_channel_id": 1429128623776075916,
        "admin_ids": [1381084245321056438, 427922282959077386, 300627668460634124, 773983223595139083, 415145467702280192],
    }
}

# PostgreSQL подключение
def get_db_connection():
    """Получает соединение с PostgreSQL из Railway"""
    database_url = os.getenv('DATABASE_URL')
    
    if not database_url:
        # Для локальной разработки
        return psycopg2.connect(
            dbname='rollback_bot',
            user='postgres',
            password='password',
            host='localhost'
        )
    
    # Для Railway
    parsed_url = urllib.parse.urlparse(database_url)
    conn = psycopg2.connect(
        database=parsed_url.path[1:],
        user=parsed_url.username,
        password=parsed_url.password,
        host=parsed_url.hostname,
        port=parsed_url.port,
        sslmode='require'
    )
    return conn

def init_db():
    """Инициализация базы данных PostgreSQL"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Таблица списков
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS lists (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            channel_id BIGINT NOT NULL,
            static_channel_id BIGINT NOT NULL,
            created_by TEXT NOT NULL,
            guild_id BIGINT NOT NULL,
            created_at TIMESTAMP NOT NULL,
            message_id BIGINT,
            status_message_id BIGINT
        )
    ''')
    
    # Таблица участников
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS participants (
            id SERIAL PRIMARY KEY,
            list_id TEXT NOT NULL REFERENCES lists(id) ON DELETE CASCADE,
            user_id TEXT NOT NULL,
            display_name TEXT NOT NULL,
            has_rollback BOOLEAN NOT NULL DEFAULT FALSE,
            registered_at TIMESTAMP NOT NULL,
            UNIQUE(list_id, user_id)
        )
    ''')
    
    # Таблица откатов
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS rollbacks (
            id SERIAL PRIMARY KEY,
            list_id TEXT NOT NULL REFERENCES lists(id) ON DELETE CASCADE,
            user_id TEXT NOT NULL,
            user_name TEXT NOT NULL,
            text TEXT NOT NULL,
            timestamp TIMESTAMP NOT NULL
        )
    ''')
    
    conn.commit()
    conn.close()
    print("✅ База данных PostgreSQL инициализирована")

# Функции для работы с базой данных
def create_new_list(list_id, list_name, channel_id, created_by, guild_id):
    """Создает новый список в базе данных"""
    config = get_server_config(guild_id)
    static_channel_id = config["static_channel_id"] if config else channel_id
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('''
        INSERT INTO lists (id, name, channel_id, static_channel_id, created_by, guild_id, created_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
    ''', (list_id, list_name, channel_id, static_channel_id, created_by, guild_id, datetime.now()))
    
    conn.commit()
    conn.close()
    
    return {
        "id": list_id,
        "name": list_name,
        "channel_id": channel_id,
        "static_channel_id": static_channel_id,
        "created_by": created_by,
        "guild_id": guild_id,
        "created_at": datetime.now().isoformat(),
        "participants": {},
        "rollbacks": {},
        "message_id": None,
        "status_message_id": None
    }

def get_list(list_id, guild_id):
    """Получает список из базы данных"""
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    cursor.execute("SELECT * FROM lists WHERE id = %s AND guild_id = %s", (list_id, guild_id))
    list_row = cursor.fetchone()
    
    if not list_row:
        conn.close()
        return None
    
    list_data = dict(list_row)
    # Конвертируем datetime в строку
    list_data["created_at"] = list_data["created_at"].isoformat()
    
    # Получаем участников
    cursor.execute("SELECT * FROM participants WHERE list_id = %s", (list_id,))
    participants = {}
    for row in cursor.fetchall():
        row_dict = dict(row)
        participants[row_dict["user_id"]] = {
            "display_name": row_dict["display_name"],
            "has_rollback": row_dict["has_rollback"],
            "registered_at": row_dict["registered_at"].isoformat()
        }
    
    # Получаем откаты
    cursor.execute("SELECT * FROM rollbacks WHERE list_id = %s", (list_id,))
    rollbacks = {}
    for row in cursor.fetchall():
        row_dict = dict(row)
        rollbacks[row_dict["timestamp"].isoformat()] = {
            "user_id": row_dict["user_id"],
            "user_name": row_dict["user_name"],
            "text": row_dict["text"],
            "timestamp": row_dict["timestamp"].isoformat()
        }
    
    conn.close()
    
    list_data["participants"] = participants
    list_data["rollbacks"] = rollbacks
    
    return list_data

def update_list_data(list_data):
    """Обновляет данные списка в базе данных"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('''
        UPDATE lists SET 
        message_id = %s, status_message_id = %s
        WHERE id = %s
    ''', (list_data.get("message_id"), list_data.get("status_message_id"), list_data["id"]))
    
    conn.commit()
    conn.close()

def register_participant(list_id, user_id, display_name):
    """Регистрирует участника в списке"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        cursor.execute('''
            INSERT INTO participants (list_id, user_id, display_name, has_rollback, registered_at)
            VALUES (%s, %s, %s, %s, %s)
        ''', (list_id, user_id, display_name, False, datetime.now()))
        conn.commit()
        return True
    except psycopg2.IntegrityError:
        return False
    finally:
        conn.close()

def remove_participant(list_id, user_id):
    """Удаляет участника из списка"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("DELETE FROM participants WHERE list_id = %s AND user_id = %s", (list_id, user_id))
    cursor.execute("DELETE FROM rollbacks WHERE list_id = %s AND user_id = %s", (list_id, user_id))
    
    conn.commit()
    conn.close()

def add_rollback(list_id, user_id, user_name, text):
    """Добавляет откат в базу данных"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    timestamp = datetime.now()
    
    cursor.execute('''
        INSERT INTO rollbacks (list_id, user_id, user_name, text, timestamp)
        VALUES (%s, %s, %s, %s, %s)
    ''', (list_id, user_id, user_name, text, timestamp))
    
    cursor.execute('''
        UPDATE participants SET has_rollback = TRUE 
        WHERE list_id = %s AND user_id = %s
    ''', (list_id, user_id))
    
    conn.commit()
    conn.close()
    
    return timestamp.isoformat()

def remove_user_rollback(list_id, user_id):
    """Удаляет откат пользователя"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("DELETE FROM rollbacks WHERE list_id = %s AND user_id = %s", (list_id, user_id))
    cursor.execute('''
        UPDATE participants SET has_rollback = FALSE 
        WHERE list_id = %s AND user_id = %s
    ''', (list_id, user_id))
    
    conn.commit()
    conn.close()
    return True

def get_all_lists(guild_id):
    """Получает все списки для сервера"""
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    cursor.execute("SELECT * FROM lists WHERE guild_id = %s", (guild_id,))
    lists = [dict(row) for row in cursor.fetchall()]
    
    for list_item in lists:
        cursor.execute("SELECT COUNT(*) as count FROM participants WHERE list_id = %s", (list_item["id"],))
        list_item["participants_count"] = cursor.fetchone()["count"]
        
        cursor.execute("SELECT COUNT(*) as count FROM participants WHERE list_id = %s AND has_rollback = TRUE", (list_item["id"],))
        list_item["rollbacks_count"] = cursor.fetchone()["count"]
    
    conn.close()
    return lists

def delete_list_from_db(list_id):
    """Удаляет список из базы данных"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("DELETE FROM lists WHERE id = %s", (list_id,))
    conn.commit()
    conn.close()

def reset_list_rollbacks(list_id):
    """Сбрасывает все откаты в списке"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("DELETE FROM rollbacks WHERE list_id = %s", (list_id,))
    cursor.execute('''
        UPDATE participants SET has_rollback = FALSE 
        WHERE list_id = %s
    ''', (list_id,))
    
    conn.commit()
    conn.close()

# Остальные функции без изменений
def get_server_config(guild_id):
    return SERVER_CONFIGS.get(guild_id)

def is_admin(member):
    if not member:
        return False
    
    config = get_server_config(member.guild.id)
    if not config:
        return False
    
    if member.guild.id == 1429544000188317831:
        try:
            member_role_ids = [role.id for role in member.roles]
            return any(role_id in config["admin_role_ids"] for role_id in member_role_ids)
        except:
            return False
    elif member.guild.id == 1003525677640851496:
        try:
            return member.id in config["admin_ids"]
        except:
            return False
    
    return False

def generate_list_id():
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=5))

def clean_rollback_text(text):
    if not text:
        return ""
    
    clean_text = re.sub(r'<[^>]+>', '', text)
    clean_text = re.sub(r'\s+', ' ', clean_text)
    clean_text = clean_text.strip()
    
    return clean_text

async def update_status_message(list_data):
    """Обновляет сообщение со статусом откатов в СТАТИЧЕСКОМ канале"""
    try:
        config = get_server_config(list_data["guild_id"])
        if not config:
            return
            
        channel_id = config["static_channel_id"]
        channel = bot.get_channel(channel_id)
        if not channel:
            return
        
        # Формируем содержимое сообщения
        total_participants = len(list_data['participants'])
        completed_rollbacks = sum(1 for p in list_data['participants'].values() if p['has_rollback'])
        
        message_content = f"📊 **СТАТУС ОТКАТОВ: {list_data['name']}**\n\n"
        message_content += f"📋 ID списка: `{list_data['id']}`\n"
        message_content += f"👥 Всего участников: **{total_participants}**\n"
        message_content += f"✅ Отправили откат: **{completed_rollbacks}** / **{total_participants}**\n"
        message_content += f"{'='*50}\n\n"
        
        if not list_data['participants']:
            message_content += "*Список участников пуст*\n"
        else:
            for user_id, participant in sorted(list_data['participants'].items(), key=lambda x: x[1]['registered_at']):
                status = "🟢" if participant['has_rollback'] else "🔴"
                username = participant['display_name']
                message_content += f"{status} **{username}**\n"
                
                if participant['has_rollback']:
                    user_rollback = None
                    for rollback in list_data['rollbacks'].values():
                        if rollback['user_id'] == user_id:
                            user_rollback = rollback
                            break
                    if user_rollback:
                        rollback_text = user_rollback['text']
                        if rollback_text:
                            rollback_preview = rollback_text[:150]
                            if len(rollback_text) > 150:
                                rollback_preview += "..."
                            message_content += f"  └ 📝 {rollback_preview}\n"
                message_content += "\n"
        
        # Проверяем, есть ли уже сообщение со статусом
        status_message_id = list_data.get("status_message_id")
        
        if status_message_id:
            try:
                status_message = await channel.fetch_message(status_message_id)
                await status_message.edit(content=message_content)
                return
            except:
                pass
        
        # Создаём новое сообщение
        new_message = await channel.send(message_content)
        list_data["status_message_id"] = new_message.id
        update_list_data(list_data)
        
    except Exception as e:
        print(f"Ошибка при обновлении статуса списка {list_data['id']}: {e}")

class CreateListModal(disnake.ui.Modal):
    def __init__(self, guild_id):
        self.guild_id = guild_id
        components = [
            disnake.ui.TextInput(
                label="Время",
                placeholder="Укажите время (например: 18:00)",
                custom_id="time",
                style=TextInputStyle.short,
                max_length=10,
                required=True
            ),
            disnake.ui.TextInput(
                label="Дата",
                placeholder="Укажите дату (например: 25.10.2025)",
                custom_id="date",
                style=TextInputStyle.short,
                max_length=20,
                required=True
            ),
            disnake.ui.TextInput(
                label="Название",
                placeholder="Название события",
                custom_id="name",
                style=TextInputStyle.short,
                max_length=50,
                required=True
            ),
            disnake.ui.TextInput(
                label="Сервер события",
                placeholder="Название сервера",
                custom_id="event_server",
                style=TextInputStyle.short,
                max_length=50,
                required=True
            )
        ]
        super().__init__(title="Создание нового списка", components=components)

    async def callback(self, inter: disnake.ModalInteraction):
        time_value = inter.text_values["time"].strip()
        date_value = inter.text_values["date"].strip()
        name_value = inter.text_values["name"].strip()
        server_value = inter.text_values["event_server"].strip()
        
        # Генерируем уникальный 5-символьный ID
        list_id = generate_list_id()
        
        # Проверяем уникальность ID в БД
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM lists WHERE id = ?", (list_id,))
        while cursor.fetchone():
            list_id = generate_list_id()
            cursor.execute("SELECT id FROM lists WHERE id = ?", (list_id,))
        conn.close()
        
        full_name = f"{time_value} | {date_value} | {name_value} | {server_value}"
        
        # Используем канал, где вызвана команда - для списка с кнопками
        channel_id = inter.channel_id
        
        # Создаём список в БД
        list_data = create_new_list(list_id, full_name, channel_id, str(inter.author.id), self.guild_id)
        
        config = get_server_config(self.guild_id)
        static_channel_mention = f"<#{config['static_channel_id']}>" if config else "не указан"
        
        await inter.response.send_message(
            f"✅ Список создан!\n"
            f"ID: `{list_id}`\n"
            f"Название: {full_name}\n"
            f"Канал с кнопками: {inter.channel.mention}\n"
            f"Статус откатов: {static_channel_mention}\n\n"
            f"Для регистрации участников используйте:\n"
            f"`/register_user list_id:{list_id} users:@участник1 @участник2`",
            ephemeral=True
        )
        
        # Создаем сообщения:
        await update_participants_message(inter.channel, list_data)
        await update_status_message(list_data)

class RollbackModal(disnake.ui.Modal):
    def __init__(self, list_id, guild_id, has_existing_rollback=False):
        self.list_id = list_id
        self.guild_id = guild_id
        self.has_existing_rollback = has_existing_rollback
        
        placeholder = "Опишите подробно вашу идею или предложение..."
        if has_existing_rollback:
            placeholder = "Ваш старый откат будет заменен на новый..."
        
        components = [
            disnake.ui.TextInput(
                label="Ваш откат",
                placeholder=placeholder,
                custom_id="rollback_text",
                style=TextInputStyle.paragraph,
                max_length=2000,
                required=True
            )
        ]
        
        title = "Заменить откат" if has_existing_rollback else "Отправить откат"
        super().__init__(title=title, components=components)

    async def callback(self, inter: disnake.ModalInteraction):
        list_data = get_list(self.list_id, self.guild_id)
        if not list_data:
            await inter.response.send_message("❌ Список не найден!", ephemeral=True)
            return
            
        user_id = str(inter.author.id)
        
        if user_id not in list_data["participants"]:
            await inter.response.send_message(
                "❌ Вы не зарегистрированы в этом списке! Обратитесь к администратору.",
                ephemeral=True
            )
            return
            
        rollback_text = inter.text_values["rollback_text"]
        
        # Очищаем текст от форматирования
        cleaned_text = clean_rollback_text(rollback_text)
        
        if not cleaned_text:
            await inter.response.send_message(
                "❌ Текст отката не может быть пустым! Пожалуйста, напишите ваш откат текстом, а не только ссылками.",
                ephemeral=True
            )
            return
        
        # Обновляем серверный никнейм участника
        server_nickname = inter.author.display_name
        
        # Удаляем старый откат, если он есть
        if self.has_existing_rollback:
            remove_user_rollback(self.list_id, user_id)
        
        # Добавляем новый откат
        add_rollback(self.list_id, user_id, server_nickname, cleaned_text)
        
        # Обновляем данные списка
        updated_list_data = get_list(self.list_id, self.guild_id)
        
        if self.has_existing_rollback:
            message = f"✅ Ваш откат в списке '{list_data['name']}' заменен на новый! Статус обновлен."
        else:
            message = f"✅ Ваш откат отправлен в список '{list_data['name']}'! Статус обновлен."
            
        await inter.response.send_message(message, ephemeral=True)
        
        # Обновляем оба сообщения:
        channel = bot.get_channel(updated_list_data["channel_id"])
        if channel:
            await update_participants_message(channel, updated_list_data)
        await update_status_message(updated_list_data)

class DeleteRollbackView(disnake.ui.View):
    def __init__(self, list_id, guild_id):
        super().__init__(timeout=60)
        self.list_id = list_id
        self.guild_id = guild_id
    
    @disnake.ui.button(label="Да, удалить мой откат", style=disnake.ButtonStyle.danger)
    async def confirm_button(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        list_data = get_list(self.list_id, self.guild_id)
        if not list_data:
            await inter.response.send_message("❌ Список не найден!", ephemeral=True)
            return
            
        user_id = str(inter.author.id)
        
        if user_id not in list_data["participants"]:
            await inter.response.send_message("❌ Вы не зарегистрированы в этом списке!", ephemeral=True)
            return
            
        if not list_data["participants"][user_id]["has_rollback"]:
            await inter.response.send_message("❌ У вас нет отправленного отката!", ephemeral=True)
            return
        
        # Удаляем откат
        if remove_user_rollback(self.list_id, user_id):
            updated_list_data = get_list(self.list_id, self.guild_id)
            
            await inter.response.send_message(
                f"✅ Ваш откат удален из списка '{list_data['name']}'!", 
                ephemeral=True
            )
            
            # Обновляем сообщения
            channel = bot.get_channel(updated_list_data["channel_id"])
            if channel:
                await update_participants_message(channel, updated_list_data)
            await update_status_message(updated_list_data)
        else:
            await inter.response.send_message("❌ Не удалось удалить откат!", ephemeral=True)
        
        # Удаляем кнопки после использования
        await inter.message.delete()
    
    @disnake.ui.button(label="Отмена", style=disnake.ButtonStyle.secondary)
    async def cancel_button(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        await inter.response.send_message("❌ Удаление отката отменено.", ephemeral=True)
        await inter.message.delete()

async def update_participants_message(channel, list_data):
    """Обновляет сообщение со списком участников и кнопками в канале списка"""
    if not list_data:
        return
    
    if list_data.get("message_id"):
        try:
            message = await channel.fetch_message(list_data["message_id"])
            embed = disnake.Embed(
                title=f"📋 {list_data['name']}",
                description=await generate_participants_list(list_data),
                color=0x2b2d31
            )
            embed.set_footer(text=f"ID: {list_data['id']} | Регистрация через администратора")
            
            # Создаем новое View каждый раз
            view = MainView(list_data["id"], list_data["guild_id"])
            await message.edit(embed=embed, view=view)
            return
        except:
            pass
    
    # Создаём новое сообщение
    embed = disnake.Embed(
        title=f"📋 {list_data['name']}",
        description=await generate_participants_list(list_data),
        color=0x2b2d31
    )
    embed.set_footer(text=f"ID: {list_data['id']} | Регистрация через администратора")
    
    # Создаем новое View
    view = MainView(list_data["id"], list_data["guild_id"])
    message = await channel.send(embed=embed, view=view)
    
    # Обновляем message_id в БД
    list_data["message_id"] = message.id
    update_list_data(list_data)

async def generate_participants_list(list_data):
    if not list_data or not list_data["participants"]:
        return "*Список участников пуст*"
    
    participants = list_data["participants"]
    sorted_participants = sorted(
        participants.items(), 
        key=lambda x: x[1]["registered_at"]
    )
    
    lines = []
    for user_id, info in sorted_participants:
        status = "✅" if info["has_rollback"] else "❌"
        mention = f"<@{user_id}>"
        lines.append(f"{status} {mention}")
    
    return "\n".join(lines)

class MainView(disnake.ui.View):
    def __init__(self, list_id, guild_id):
        super().__init__(timeout=None)
        self.list_id = list_id
        self.guild_id = guild_id
    
    @disnake.ui.button(label="Отправить откат", style=disnake.ButtonStyle.primary)
    async def rollback_button(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        list_data = get_list(self.list_id, self.guild_id)
        if not list_data:
            await inter.response.send_message("❌ Список не найден!", ephemeral=True)
            return
            
        user_id = str(inter.author.id)
        if user_id not in list_data["participants"]:
            await inter.response.send_message(
                "❌ Вы не зарегистрированы в этом списке! Обратитесь к администратору.",
                ephemeral=True
            )
            return
        
        # Проверяем, есть ли уже откат у пользователя
        has_existing_rollback = list_data["participants"][user_id]["has_rollback"]
        
        if has_existing_rollback:
            # Создаем отдельный класс для кнопок выбора
            class ChoiceView(disnake.ui.View):
                def __init__(self, list_id, guild_id):
                    super().__init__(timeout=60)
                    self.list_id = list_id
                    self.guild_id = guild_id
                
                @disnake.ui.button(label="Заменить откат", style=disnake.ButtonStyle.primary)
                async def replace_button(self, button: disnake.ui.Button, interaction: disnake.MessageInteraction):
                    await interaction.response.send_modal(RollbackModal(self.list_id, self.guild_id, has_existing_rollback=True))
                
                @disnake.ui.button(label="Удалить откат", style=disnake.ButtonStyle.danger)
                async def delete_button(self, button: disnake.ui.Button, interaction: disnake.MessageInteraction):
                    delete_view = DeleteRollbackView(self.list_id, self.guild_id)
                    await interaction.response.send_message(
                        "❓ Вы уверены, что хотите удалить свой откат?",
                        view=delete_view,
                        ephemeral=True
                    )
                
                @disnake.ui.button(label="Отмена", style=disnake.ButtonStyle.secondary)
                async def cancel_button(self, button: disnake.ui.Button, interaction: disnake.MessageInteraction):
                    await interaction.response.send_message("❌ Действие отменено.", ephemeral=True)
            
            choice_view = ChoiceView(self.list_id, self.guild_id)
            
            await inter.response.send_message(
                "📝 У вас уже есть отправленный откат. Что вы хотите сделать?",
                view=choice_view,
                ephemeral=True
            )
        else:
            # Если отката нет, просто отправляем модальное окно
            await inter.response.send_modal(RollbackModal(self.list_id, self.guild_id, has_existing_rollback=False))
    
    @disnake.ui.button(label="Обновить список", style=disnake.ButtonStyle.secondary)
    async def refresh_button(self, button: disnake.ui.Button, inter: disnake.MessageInteraction):
        await inter.response.defer(ephemeral=True)
        list_data = get_list(self.list_id, self.guild_id)
        if not list_data:
            await inter.followup.send("❌ Список не найден!", ephemeral=True)
            return
            
        # Обновляем оба сообщения:
        channel = bot.get_channel(list_data["channel_id"])
        if channel:
            await update_participants_message(channel, list_data)
        await update_status_message(list_data)
        await inter.edit_original_response(content="✅ Оба списка обновлены!")

@bot.event
async def on_ready():
    print(f'Bot {bot.user} готов к работе!')
    print(f'Подключен к {len(bot.guilds)} серверам')
    print("Поддерживаемые серверы:")
    for guild_id, config in SERVER_CONFIGS.items():
        print(f"- Сервер {guild_id}")
    
    # Инициализируем БД
    init_db()
    print("✅ Бот запущен и готов к работе!")

@bot.slash_command(description="Создать новый список откатов")
async def create_list(inter: disnake.ApplicationCommandInteraction):
    if not is_admin(inter.author):
        await inter.response.send_message("❌ У вас нет прав для выполнения этой команды!", ephemeral=True)
        return
    
    await inter.response.send_modal(CreateListModal(inter.guild.id))

@bot.slash_command(description="Регистрировать пользователей в списке")
async def register_user(
    inter: disnake.ApplicationCommandInteraction,
    list_id: str = commands.Param(description="ID списка"),
    users: str = commands.Param(description="Пользователи через @ или ID через пробел")
):
    if not is_admin(inter.author):
        await inter.response.send_message("❌ У вас нет прав для выполнения этой команды!", ephemeral=True)
        return
    
    list_data = get_list(list_id, inter.guild.id)
    if not list_data:
        await inter.response.send_message("❌ Список с таким ID не найден!", ephemeral=True)
        return
    
    # Парсинг пользователей из строки
    user_mentions = re.findall(r'<@!?(\d+)>', users)
    user_ids = re.findall(r'\b(\d{17,19})\b', users)
    
    all_user_ids = list(set(user_mentions + user_ids))
    
    if not all_user_ids:
        await inter.response.send_message("❌ Не найдено ни одного валидного пользователя!", ephemeral=True)
        return
    
    registered_users = []
    already_registered = []
    
    for user_id in all_user_ids:
        try:
            member = inter.guild.get_member(int(user_id))
            if not member:
                member = await bot.fetch_user(int(user_id))
            
            server_nickname = member.display_name
            
            if not register_participant(list_id, user_id, server_nickname):
                already_registered.append(server_nickname)
            else:
                registered_users.append(server_nickname)
        except:
            continue
    
    if registered_users or already_registered:
        # Обновляем данные списка
        updated_list_data = get_list(list_id, inter.guild.id)
        
        response = []
        if registered_users:
            response.append(f"✅ Зарегистрированы: {', '.join(registered_users)}")
        if already_registered:
            response.append(f"ℹ️ Уже были зарегистрированы: {', '.join(already_registered)}")
        
        await inter.response.send_message("\n".join(response), ephemeral=True)
        
        # Обновляем оба сообщения:
        channel = bot.get_channel(updated_list_data["channel_id"])
        if channel:
            await update_participants_message(channel, updated_list_data)
        await update_status_message(updated_list_data)
    else:
        await inter.response.send_message("❌ Не удалось зарегистрировать ни одного пользователя!", ephemeral=True)

@bot.slash_command(description="Показать список откатов")
async def show_list(
    inter: disnake.ApplicationCommandInteraction,
    list_id: str = commands.Param(description="ID списка")
):
    list_data = get_list(list_id, inter.guild.id)
    if not list_data:
        await inter.response.send_message("❌ Список с таким ID не найден!", ephemeral=True)
        return
    
    await inter.response.defer()
    
    # Создаем временное сообщение в текущем канале
    embed = disnake.Embed(
        title=f"📋 {list_data['name']}",
        description=await generate_participants_list(list_data),
        color=0x2b2d31
    )
    embed.set_footer(text=f"ID: {list_data['id']} | Регистрация через администратора")
    
    await inter.edit_original_response(
        content=f"✅ Список '{list_data['name']}' отображен!",
        embed=embed,
        view=MainView(list_data["id"], inter.guild.id)
    )

@bot.slash_command(description="Удалить пользователя из списка")
async def remove_user(
    inter: disnake.ApplicationCommandInteraction,
    list_id: str = commands.Param(description="ID списка"),
    user: disnake.User = commands.Param(description="Пользователь для удаления")
):
    if not is_admin(inter.author):
        await inter.response.send_message("❌ У вас нет прав для выполнения этой команды!", ephemeral=True)
        return
    
    list_data = get_list(list_id, inter.guild.id)
    if not list_data:
        await inter.response.send_message("❌ Список с таким ID не найден!", ephemeral=True)
        return
    
    user_id = str(user.id)
    if user_id not in list_data["participants"]:
        await inter.response.send_message("❌ Пользователь не зарегистрирован в этом списке!", ephemeral=True)
        return
    
    # Получаем серверный никнейм
    member = inter.guild.get_member(user.id)
    server_nickname = member.display_name if member else user.display_name
    
    # Удаляем пользователя из БД
    remove_participant(list_id, user_id)
    
    # Обновляем данные списка
    updated_list_data = get_list(list_id, inter.guild.id)
    
    await inter.response.send_message(f"✅ Пользователь {server_nickname} удален из списка '{list_data['name']}'!", ephemeral=True)
    
    # Обновляем оба сообщения:
    channel = bot.get_channel(updated_list_data["channel_id"])
    if channel:
        await update_participants_message(channel, updated_list_data)
    await update_status_message(updated_list_data)

@bot.slash_command(description="Удалить весь список")
async def delete_list(
    inter: disnake.ApplicationCommandInteraction,
    list_id: str = commands.Param(description="ID списка для удаления")
):
    if not is_admin(inter.author):
        await inter.response.send_message("❌ У вас нет прав для выполнения этой команды!", ephemeral=True)
        return
    
    list_data = get_list(list_id, inter.guild.id)
    if not list_data:
        await inter.response.send_message("❌ Список с таким ID не найден!", ephemeral=True)
        return
    
    # Удаляем список из БД (используем переименованную функцию)
    delete_list_from_db(list_id)
    
    await inter.response.send_message(f"✅ Список '{list_data['name']}' (ID: {list_id}) полностью удален!", ephemeral=True)

@bot.slash_command(description="Сбросить откаты всех участников")
async def reset_rollbacks(
    inter: disnake.ApplicationCommandInteraction,
    list_id: str = commands.Param(description="ID списка")
):
    if not is_admin(inter.author):
        await inter.response.send_message("❌ У вас нет прав для выполнения этой команды!", ephemeral=True)
        return
    
    list_data = get_list(list_id, inter.guild.id)
    if not list_data:
        await inter.response.send_message("❌ Список с таким ID не найден!", ephemeral=True)
        return
    
    # Сбрасываем откаты в БД
    reset_list_rollbacks(list_id)
    
    # Обновляем данные списка
    updated_list_data = get_list(list_id, inter.guild.id)
    
    await inter.response.send_message(f"✅ Все откаты в списке '{list_data['name']}' сброшены!", ephemeral=True)
    
    # Обновляем оба сообщения:
    channel = bot.get_channel(updated_list_data["channel_id"])
    if channel:
        await update_participants_message(channel, updated_list_data)
    await update_status_message(updated_list_data)

@bot.slash_command(description="Посмотреть все списки")
async def list_all(inter: disnake.ApplicationCommandInteraction):
    if not is_admin(inter.author):
        await inter.response.send_message("❌ У вас нет прав для выполнения этой команды!", ephemeral=True)
        return
    
    lists_data = get_all_lists(inter.guild.id)
    
    if not lists_data:
        await inter.response.send_message("📋 Списков пока нет!", ephemeral=True)
        return
    
    embed = disnake.Embed(title="📋 Все списки", color=0x2b2d31)
    
    for list_data in lists_data:
        embed.add_field(
            name=f"{list_data['name']} (ID: {list_data['id']})",
            value=f"Участников: {list_data['participants_count']}\nОткатов: {list_data['rollbacks_count']}",
            inline=True
        )
    
    await inter.response.send_message(embed=embed, ephemeral=True)

if __name__ == "__main__":
    # Инициализируем БД
    init_db()
    
    token = os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        print("❌ DISCORD_BOT_TOKEN не найден!")
        input("Нажмите Enter для выхода...")
    else:
        try:
            print("🔄 Запуск бота...")
            bot.run(token)
        except Exception as e:
            print(f"❌ Ошибка при запуске бота: {e}")
            input("Нажмите Enter для выхода...")