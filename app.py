import os
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify # Добавили jsonify
from werkzeug.security import generate_password_hash, check_password_hash
from flask_sqlalchemy import SQLAlchemy # Добавили SQLAlchemy
from datetime import datetime # Для временных меток

app = Flask(__name__)
app.secret_key = os.urandom(24)

# --- Конфигурация Базы Данных ---
basedir = os.path.abspath(os.path.dirname(__file__))
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(basedir, 'exchange.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False # Отключаем ненужное отслеживание
db = SQLAlchemy(app)
# -------------------------------

# --- Модели Базы Данных ---
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(128), nullable=False)
    # Балансы теперь в отдельной таблице для гибкости
    balances = db.relationship('Balance', backref='user', lazy=True)
    transactions = db.relationship('Transaction', backref='user', lazy=True)

    def __repr__(self):
        return f'<User {self.username}>'

class Balance(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    currency = db.Column(db.String(10), nullable=False) # 'USD', 'BTC', 'ETH', 'DOGE'
    amount = db.Column(db.Float, nullable=False, default=0.0)

    # Уникальность пары пользователь-валюта
    __table_args__ = (db.UniqueConstraint('user_id', 'currency', name='_user_currency_uc'),)

    def __repr__(self):
        return f'<Balance {self.user.username} {self.currency} {self.amount}>'

class Transaction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    type = db.Column(db.String(10), nullable=False) # 'buy', 'sell', 'deposit', 'withdraw'
    currency_from = db.Column(db.String(10)) # e.g., 'USD' for buy, 'BTC' for sell
    amount_from = db.Column(db.Float)
    currency_to = db.Column(db.String(10)) # e.g., 'BTC' for buy, 'USD' for sell
    amount_to = db.Column(db.Float)
    price = db.Column(db.Float) # Цена за единицу currency_to в currency_from
    timestamp = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    def __repr__(self):
        return f'<Transaction {self.user.username} {self.type} {self.amount_to} {self.currency_to}>'

# --------------------------

# --- Имитация Цен (пока оставим) ---
mock_prices = {
    'BTC': 50000.0,
    'ETH': 4000.0,
    'DOGE': 0.15
}
AVAILABLE_CRYPTO = list(mock_prices.keys())
# --------------------------------------

# --- Функции-помощники для работы с балансами ---
def get_balance(user_id, currency):
    """Получает или создает баланс пользователя для валюты."""
    balance = Balance.query.filter_by(user_id=user_id, currency=currency).first()
    if not balance:
        # Создаем запись с нулевым балансом, если ее нет
        balance = Balance(user_id=user_id, currency=currency, amount=0.0)
        db.session.add(balance)
        # Коммит здесь не нужен, будет сделан в конце запроса или при обновлении
    return balance

def update_balance(user_id, currency, new_amount):
    """Обновляет баланс пользователя."""
    balance = get_balance(user_id, currency)
    balance.amount = new_amount
    # Коммит будет вызван после всех операций в запросе

def get_all_balances(user_id):
    """Получает все балансы пользователя в виде словаря."""
    balances_db = Balance.query.filter_by(user_id=user_id).all()
    user_balances = {b.currency: b.amount for b in balances_db}
    # Добавим нулевые балансы для всех доступных крипто и USD, если их нет
    for coin in AVAILABLE_CRYPTO:
        user_balances.setdefault(coin, 0.0)
    user_balances.setdefault('USD', 0.0)
    return user_balances

# --- Маршруты ---

@app.route('/')
def index():
    """Главная страница - показывает текущие цены."""
    return render_template('index.html', prices=mock_prices)

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')

        if not username or not password:
            flash('Имя пользователя и пароль обязательны!', 'error')
            return redirect(url_for('register'))

        # Проверяем, существует ли пользователь
        existing_user = User.query.filter_by(username=username).first()
        if existing_user:
            flash('Пользователь с таким именем уже существует!', 'error')
            return redirect(url_for('register'))

        password_hash = generate_password_hash(password)
        new_user = User(username=username, password_hash=password_hash)

        try:
            db.session.add(new_user)
            db.session.flush() # Получаем ID нового пользователя

            # Создаем начальный баланс USD
            initial_usd_balance = Balance(user_id=new_user.id, currency='USD', amount=1000.0)
            db.session.add(initial_usd_balance)

            # Создаем нулевые балансы для криптовалют
            for coin in AVAILABLE_CRYPTO:
                crypto_balance = Balance(user_id=new_user.id, currency=coin, amount=0.0)
                db.session.add(crypto_balance)

            db.session.commit() # Сохраняем все изменения
            flash('Регистрация успешна! Теперь вы можете войти.', 'success')
            return redirect(url_for('login'))
        except Exception as e:
            db.session.rollback() # Откатываем изменения в случае ошибки
            flash(f'Произошла ошибка при регистрации: {e}', 'error')
            app.logger.error(f"Registration error: {e}") # Логируем ошибку
            return redirect(url_for('register'))

    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')

        user = User.query.filter_by(username=username).first()

        if not user or not check_password_hash(user.password_hash, password):
            flash('Неверное имя пользователя или пароль.', 'error')
            return redirect(url_for('login'))

        session['user_id'] = user.id # Сохраняем ID пользователя в сессии
        session['username'] = user.username
        flash('Вход выполнен успешно!', 'success')
        return redirect(url_for('dashboard'))

    return render_template('login.html')

@app.route('/dashboard', methods=['GET', 'POST'])
def dashboard():
    if 'user_id' not in session:
        flash('Пожалуйста, войдите для доступа к личному кабинету.', 'error')
        return redirect(url_for('login'))

    user_id = session['user_id']
    user = User.query.get(user_id) # Получаем объект пользователя
    if not user:
         flash('Ошибка: пользователь не найден.', 'error')
         session.clear() # Очищаем сессию
         return redirect(url_for('login'))

    # --- Обработка торговли ---
    if request.method == 'POST':
        action = request.form.get('action')
        coin = request.form.get('coin')
        amount_str = request.form.get('amount')

        if not action or not coin or not amount_str:
            flash('Не все поля формы заполнены.', 'error')
            return redirect(url_for('dashboard'))

        if coin not in AVAILABLE_CRYPTO:
             flash('Выбрана недоступная монета.', 'error')
             return redirect(url_for('dashboard'))

        try:
            amount = float(amount_str)
            if amount <= 0:
                raise ValueError("Количество должно быть положительным.")

            current_price = mock_prices.get(coin) # Используем мок-цену
            if not current_price:
                 flash('Не удалось получить цену для монеты.', 'error') # Маловероятно с mock_prices
                 return redirect(url_for('dashboard'))

            # Получаем текущие балансы
            usd_obj = get_balance(user_id, 'USD')
            coin_obj = get_balance(user_id, coin)
            user_usd = usd_obj.amount
            user_coin = coin_obj.amount

            total_usd_cost = amount * current_price

            transaction = None # Для записи в историю

            if action == 'buy':
                if user_usd >= total_usd_cost:
                    # Обновляем балансы
                    usd_obj.amount -= total_usd_cost
                    coin_obj.amount += amount
                    # Записываем транзакцию
                    transaction = Transaction(
                        user_id=user_id, type='buy',
                        currency_from='USD', amount_from=total_usd_cost,
                        currency_to=coin, amount_to=amount,
                        price=current_price
                    )
                    flash(f'Успешно куплено {amount:.8f} {coin} за {total_usd_cost:.2f} USD', 'success')
                else:
                    flash('Недостаточно USD для покупки.', 'error')

            elif action == 'sell':
                if user_coin >= amount:
                    # Обновляем балансы
                    usd_obj.amount += total_usd_cost
                    coin_obj.amount -= amount
                    # Записываем транзакцию
                    transaction = Transaction(
                        user_id=user_id, type='sell',
                        currency_from=coin, amount_from=amount,
                        currency_to='USD', amount_to=total_usd_cost,
                        price=current_price
                    )
                    flash(f'Успешно продано {amount:.8f} {coin} за {total_usd_cost:.2f} USD', 'success')
                else:
                    flash(f'Недостаточно {coin} для продажи.', 'error')
            else:
                 flash('Неизвестное действие.', 'error')

            if transaction:
                try:
                    db.session.add(transaction)
                    db.session.commit() # Сохраняем изменения балансов и транзакцию
                except Exception as e:
                    db.session.rollback()
                    flash(f'Ошибка базы данных при сохранении сделки: {e}', 'error')
                    app.logger.error(f"Trade DB error: {e}")

            return redirect(url_for('dashboard')) # Перезагружаем, чтобы показать обновленный баланс и флеш-сообщение

        except ValueError as e:
            flash(f'Неверный формат данных: {e}', 'error')
            db.session.rollback() # Откатываем возможные изменения балансов до ошибки
            return redirect(url_for('dashboard'))
        except Exception as e:
            db.session.rollback()
            flash(f'Произошла непредвиденная ошибка: {e}', 'error')
            app.logger.error(f"Dashboard POST error: {e}")
            return redirect(url_for('dashboard'))

    # --- Отображение для GET запроса ---
    user_balances = get_all_balances(user_id)

    # Передаем данные в шаблон
    # Форматирование сумм лучше делать прямо в шаблоне через |format()
    return render_template('dashboard.html',
                           user=user, # Передаем объект пользователя
                           balances=user_balances, # Передаем словарь балансов
                           prices=mock_prices) # Передаем цены

# --- Маршрут для истории транзакций ---
@app.route('/history')
def history():
    if 'user_id' not in session:
        flash('Пожалуйста, войдите для просмотра истории.', 'error')
        return redirect(url_for('login'))

    user_id = session['user_id']
    # Получаем транзакции пользователя, сортируем по убыванию времени
    user_transactions = Transaction.query.filter_by(user_id=user_id)\
                                     .order_by(Transaction.timestamp.desc())\
                                     .all()

    return render_template('history.html', transactions=user_transactions)
# -------------------------------------

@app.route('/logout')
def logout():
    session.pop('user_id', None)
    session.pop('username', None)
    flash('Вы успешно вышли.', 'success')
    return redirect(url_for('index'))

# --- Создание таблиц БД (если их нет) ---
# Это нужно выполнить один раз перед первым запуском или если модели изменились
# Можно сделать отдельный скрипт или выполнить в Python консоли
@app.cli.command('init-db')
def init_db():
    """Создает таблицы базы данных."""
    with app.app_context(): # !!! Важно для Flask >= 2.0 !!!
        db.create_all()
    print("База данных инициализирована!")

# Команда для запуска из терминала: flask init-db
# ---------------------------------------

# --- Контекстный процессор для передачи года в шаблон (улучшение футера) ---
@app.context_processor
def inject_now():
    return {'now': datetime.utcnow()}
# -------------------------------------------------------------------

if __name__ == '__main__':
    # Важно: Создайте БД перед первым запуском!
    # Откройте терминал в папке проекта и выполните: flask init-db
    app.run(debug=True) # Debug=True НЕ для продакшена!
