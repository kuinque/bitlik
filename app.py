import os
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
from decimal import Decimal, ROUND_DOWN # Используем Decimal для точности финансов

# --- Базовая настройка (как раньше) ---
app = Flask(__name__)
app.secret_key = os.urandom(24)
basedir = os.path.abspath(os.path.dirname(__file__))
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(basedir, 'exchange.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# --- Настройки Точности ---
# Контекст для Decimal (8 знаков после запятой для крипты, 2 для USD)
CRYPTO_PRECISION = Decimal('0.00000001')
USD_PRECISION = Decimal('0.01')

def quantize_usd(d):
    return d.quantize(USD_PRECISION, rounding=ROUND_DOWN)

def quantize_crypto(d):
    return d.quantize(CRYPTO_PRECISION, rounding=ROUND_DOWN)

# --- Обновленные Модели Базы Данных ---

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(128), nullable=False)
    balances = db.relationship('Balance', backref='user', lazy='dynamic') # lazy='dynamic' для удобных запросов
    transactions = db.relationship('Transaction', backref='user', lazy='dynamic')
    p2p_orders = db.relationship('P2POrder', backref='user', lazy='dynamic')

    def get_available_balance(self, currency):
        """Возвращает доступный баланс (общий - замороженный в P2P ордерах)."""
        total_balance_obj = self.balances.filter_by(currency=currency).first()
        total_balance = Decimal(str(total_balance_obj.amount)) if total_balance_obj else Decimal('0.0')

        frozen_amount = Decimal('0.0')
        if currency == 'USD':
            # Заморожено в ордерах на покупку крипты
            buy_orders = self.p2p_orders.filter_by(order_type='buy', status='open').all()
            for order in buy_orders:
                order_cost = Decimal(str(order.amount_crypto_remaining)) * Decimal(str(order.price_per_unit))
                frozen_amount += order_cost
            return quantize_usd(total_balance - frozen_amount)
        else:
            # Заморожено в ордерах на продажу этой крипты
            sell_orders = self.p2p_orders.filter_by(order_type='sell', crypto_currency=currency, status='open').all()
            for order in sell_orders:
                frozen_amount += Decimal(str(order.amount_crypto_remaining))
            return quantize_crypto(total_balance - frozen_amount)



class Balance(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    currency = db.Column(db.String(10), nullable=False)
    # Используем Numeric для лучшей точности в БД с Decimal
    amount = db.Column(db.Numeric(precision=28, scale=18), nullable=False, default=Decimal('0.0'))
    __table_args__ = (db.UniqueConstraint('user_id', 'currency', name='_user_currency_uc'),)

    # Конвертация при чтении/записи Numeric в Decimal
    @property
    def amount_decimal(self):
        return Decimal(self.amount) if self.amount is not None else Decimal('0.0')

    @amount_decimal.setter
    def amount_decimal(self, value):
        self.amount = value

class Transaction(db.Model): # История рыночных сделок (как раньше)
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    type = db.Column(db.String(10), nullable=False)
    currency_from = db.Column(db.String(10))
    amount_from = db.Column(db.Numeric(precision=28, scale=18))
    currency_to = db.Column(db.String(10))
    amount_to = db.Column(db.Numeric(precision=28, scale=18))
    price = db.Column(db.Numeric(precision=28, scale=18))
    timestamp = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

# --- Новые модели для P2P ---
class P2POrder(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    order_type = db.Column(db.String(4), nullable=False) # 'buy' или 'sell'
    crypto_currency = db.Column(db.String(10), nullable=False) # 'BTC', 'ETH', 'DOGE'
    fiat_currency = db.Column(db.String(10), nullable=False, default='USD')
    amount_crypto_initial = db.Column(db.Numeric(precision=28, scale=18), nullable=False)
    amount_crypto_remaining = db.Column(db.Numeric(precision=28, scale=18), nullable=False) # Сколько крипты осталось исполнить
    price_per_unit = db.Column(db.Numeric(precision=28, scale=18), nullable=False) # Цена за 1 crypto в fiat
    status = db.Column(db.String(20), nullable=False, default='open') # 'open', 'partially_filled', 'filled', 'cancelled'
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Конвертация для удобства
    @property
    def amount_crypto_initial_decimal(self): return Decimal(self.amount_crypto_initial)
    @property
    def amount_crypto_remaining_decimal(self): return Decimal(self.amount_crypto_remaining)
    @property
    def price_per_unit_decimal(self): return Decimal(self.price_per_unit)

class P2PTrade(db.Model): # История исполненных P2P сделок
    id = db.Column(db.Integer, primary_key=True)
    buy_order_id = db.Column(db.Integer, db.ForeignKey('p2p_order.id'))
    sell_order_id = db.Column(db.Integer, db.ForeignKey('p2p_order.id'))
    buyer_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    seller_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    crypto_currency = db.Column(db.String(10), nullable=False)
    fiat_currency = db.Column(db.String(10), nullable=False, default='USD')
    amount_crypto = db.Column(db.Numeric(precision=28, scale=18), nullable=False) # Сколько крипты было обменяно
    price_per_unit = db.Column(db.Numeric(precision=28, scale=18), nullable=False) # Цена исполнения
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

    # Связи для удобного доступа к ордерам и пользователям
    buy_order = db.relationship('P2POrder', foreign_keys=[buy_order_id], backref='trades_as_buy')
    sell_order = db.relationship('P2POrder', foreign_keys=[sell_order_id], backref='trades_as_sell')
    buyer = db.relationship('User', foreign_keys=[buyer_id], backref='p2p_trades_bought')
    seller = db.relationship('User', foreign_keys=[seller_id], backref='p2p_trades_sold')

# --- Мок-цены (для рыночной торговли) ---
mock_prices = {
    'BTC': Decimal('50000.0'),
    'ETH': Decimal('4000.0'),
    'DOGE': Decimal('0.15')
}
AVAILABLE_CRYPTO = list(mock_prices.keys())

# --- Функции-помощники для балансов (с Decimal) ---
def get_balance(user_id, currency):
    balance = Balance.query.filter_by(user_id=user_id, currency=currency).first()
    if not balance:
        balance = Balance(user_id=user_id, currency=currency, amount=Decimal('0.0'))
        db.session.add(balance)
    return balance

def update_balance(user_id, currency, change_amount: Decimal):
    """Изменяет баланс на указанную сумму (может быть отрицательной)."""
    balance = get_balance(user_id, currency)
    # Убедимся, что работаем с Decimal
    current_amount = Decimal(balance.amount) if balance.amount is not None else Decimal('0.0')
    new_amount = current_amount + change_amount
    # Проверка на отрицательный баланс (можно добавить позже, если нужно)
    # if new_amount < Decimal('0.0'):
    #     raise ValueError(f"Insufficient balance for {currency}")
    balance.amount = new_amount # SQLAlchemy обработает Decimal -> Numeric
    return balance # Возвращаем объект для возможного chaining

def get_all_balances(user_id):
    user = User.query.get(user_id)
    if not user:
        return {}
    balances_db = user.balances.all()
    user_balances = {b.currency: Decimal(b.amount) for b in balances_db}
    user_available_balances = {}
    all_currencies = ['USD'] + AVAILABLE_CRYPTO
    for curr in all_currencies:
        user_balances.setdefault(curr, Decimal('0.0'))
        user_available_balances[curr] = user.get_available_balance(curr)

    return {
        'total': user_balances,
        'available': user_available_balances
    }


# --- Маршруты (существующие обновлены, добавлены новые) ---

# ... (/, /register, /login - немного изменить):
# В /register - инициализировать балансы Decimal('0.0') или Decimal('1000.0') для USD
# В /login - используем session['user_id']
# В /logout - удаляем session['user_id']

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        # ... (проверки как раньше) ...
        existing_user = User.query.filter_by(username=request.form.get('username')).first()
        if existing_user:
            flash('Пользователь с таким именем уже существует!', 'error')
            return redirect(url_for('register'))

        password_hash = generate_password_hash(request.form.get('password'))
        new_user = User(username=request.form.get('username'), password_hash=password_hash)

        try:
            db.session.add(new_user)
            db.session.flush() # Получаем ID

            # Создаем начальные балансы с Decimal
            initial_usd_balance = Balance(user_id=new_user.id, currency='USD', amount=Decimal('1000.0'))
            db.session.add(initial_usd_balance)
            for coin in AVAILABLE_CRYPTO:
                crypto_balance = Balance(user_id=new_user.id, currency=coin, amount=Decimal('0.0'))
                db.session.add(crypto_balance)

            db.session.commit()
            flash('Регистрация успешна! Теперь вы можете войти.', 'success')
            return redirect(url_for('login'))
        except Exception as e:
            db.session.rollback()
            flash(f'Произошла ошибка при регистрации: {e}', 'error')
            app.logger.error(f"Registration error: {e}")
            return redirect(url_for('register'))
    return render_template('register.html')

@app.route('/dashboard', methods=['GET', 'POST']) # Теперь это только рыночная торговля
def dashboard():
    if 'user_id' not in session:
        flash('Пожалуйста, войдите.', 'error')
        return redirect(url_for('login'))

    user_id = session['user_id']
    user = User.query.get_or_404(user_id)

    if request.method == 'POST':
        # --- Рыночная торговля (Market Order) ---
        action = request.form.get('action') # 'buy' или 'sell'
        coin = request.form.get('coin')
        amount_str = request.form.get('amount')

        # ... (Валидация формы) ...
        if coin not in AVAILABLE_CRYPTO:
             flash('Недоступная монета.', 'error'); return redirect(url_for('dashboard'))

        try:
            amount = quantize_crypto(Decimal(amount_str)) # Конвертируем в Decimal
            if amount <= Decimal('0'): raise ValueError("Нулевое или отрицательное количество")

            current_price = mock_prices[coin] # Берем мок-цену
            total_usd_cost = quantize_usd(amount * current_price)

            # Проверяем ДОСТУПНЫЕ балансы
            available_usd = user.get_available_balance('USD')
            available_coin = user.get_available_balance(coin)

            transaction = None
            if action == 'buy':
                if available_usd >= total_usd_cost:
                    # Атомарно меняем балансы и записываем транзакцию
                    update_balance(user_id, 'USD', -total_usd_cost)
                    update_balance(user_id, coin, amount)
                    transaction = Transaction(
                        user_id=user_id, type='buy', currency_from='USD', amount_from=total_usd_cost,
                        currency_to=coin, amount_to=amount, price=current_price )
                    flash(f'Куплено {amount} {coin}', 'success')
                else:
                    flash('Недостаточно доступных USD.', 'error')
            elif action == 'sell':
                 if available_coin >= amount:
                    update_balance(user_id, coin, -amount)
                    update_balance(user_id, 'USD', total_usd_cost)
                    transaction = Transaction(
                        user_id=user_id, type='sell', currency_from=coin, amount_from=amount,
                        currency_to='USD', amount_to=total_usd_cost, price=current_price)
                    flash(f'Продано {amount} {coin}', 'success')
                 else:
                    flash(f'Недостаточно доступных {coin}.', 'error')
            # ... (обработка ошибок, commit/rollback) ...
            if transaction:
                 db.session.add(transaction)
            db.session.commit() # Коммитим изменения балансов и транзакцию

        except ValueError as e:
            db.session.rollback()
            flash(f'Ошибка ввода: {e}', 'error')
        except Exception as e:
            db.session.rollback()
            flash(f'Непредвиденная ошибка: {e}', 'error')
            app.logger.error(f"Market trade error: {e}")

        return redirect(url_for('dashboard'))

    # GET-запрос
    user_balances = get_all_balances(user_id)
    return render_template('dashboard.html',
                           user=user,
                           balances_total=user_balances['total'],
                           balances_available=user_balances['available'],
                           prices=mock_prices)


# --- Новый раздел P2P ---
@app.route('/p2p', methods=['GET'])
def p2p_page():
    if 'user_id' not in session:
        flash('Пожалуйста, войдите.', 'error')
        return redirect(url_for('login'))

    user_id = session['user_id']
    user = User.query.get_or_404(user_id)

    # Получаем свои открытые P2P ордера
    my_open_orders = P2POrder.query.filter_by(user_id=user_id, status='open') \
                                   .order_by(P2POrder.created_at.desc()).all()

    # Получаем историю своих P2P сделок
    my_p2p_trades = P2PTrade.query.filter((P2PTrade.buyer_id == user_id) | (P2PTrade.seller_id == user_id)) \
                               .order_by(P2PTrade.timestamp.desc()).limit(20).all() # Последние 20

    # Получаем "стакан" - лучшие предложения от ДРУГИХ пользователей
    # Отобразим топ 5 на покупку (bids) и топ 5 на продажу (asks) для каждой монеты
    order_book = {}
    for coin in AVAILABLE_CRYPTO:
        bids = P2POrder.query.filter(
                    P2POrder.crypto_currency == coin,
                    P2POrder.order_type == 'buy',
                    P2POrder.status == 'open',
                    P2POrder.user_id != user_id # Не свои ордера
                ).order_by(P2POrder.price_per_unit.desc(), P2POrder.created_at.asc()) \
                 .limit(5).all()

        asks = P2POrder.query.filter(
                    P2POrder.crypto_currency == coin,
                    P2POrder.order_type == 'sell',
                    P2POrder.status == 'open',
                    P2POrder.user_id != user_id # Не свои ордера
                ).order_by(P2POrder.price_per_unit.asc(), P2POrder.created_at.asc()) \
                 .limit(5).all()
        order_book[coin] = {'bids': bids, 'asks': asks}


    balances = get_all_balances(user_id) # Нужны доступные балансы для формы

    return render_template('p2p.html',
                           my_open_orders=my_open_orders,
                           my_p2p_trades=my_p2p_trades,
                           order_book=order_book,
                           available_balances=balances['available'],
                           available_crypto=AVAILABLE_CRYPTO,
                           user_id=user_id) # Передаем user_id для рендеринга истории


@app.route('/p2p/place_order', methods=['POST'])
def place_p2p_order():
    if 'user_id' not in session: return jsonify({"error": "Not logged in"}), 401
    user_id = session['user_id']
    user = User.query.get_or_404(user_id)

    try:
        order_type = request.form.get('order_type') # 'buy' или 'sell'
        crypto_currency = request.form.get('crypto_currency')
        amount_str = request.form.get('amount_crypto')
        price_str = request.form.get('price_per_unit')

        # Валидация
        if order_type not in ['buy', 'sell']: raise ValueError("Неверный тип ордера")
        if crypto_currency not in AVAILABLE_CRYPTO: raise ValueError("Неверная криптовалюта")
        if not amount_str or not price_str: raise ValueError("Заполните все поля")

        amount_crypto = quantize_crypto(Decimal(amount_str))
        price_per_unit = quantize_usd(Decimal(price_str)) # Цена в USD

        if amount_crypto <= Decimal('0') or price_per_unit <= Decimal('0'):
            raise ValueError("Количество и цена должны быть положительными")

        # Проверка доступного баланса (упрощенная)
        if order_type == 'buy':
            required_usd = quantize_usd(amount_crypto * price_per_unit)
            available_usd = user.get_available_balance('USD')
            if available_usd < required_usd:
                 raise ValueError(f"Недостаточно USD. Требуется: {required_usd}, доступно: {available_usd}")
        else: # sell
            available_crypto = user.get_available_balance(crypto_currency)
            if available_crypto < amount_crypto:
                raise ValueError(f"Недостаточно {crypto_currency}. Требуется: {amount_crypto}, доступно: {available_crypto}")

        # Создаем ордер
        new_order = P2POrder(
            user_id=user_id,
            order_type=order_type,
            crypto_currency=crypto_currency,
            fiat_currency='USD',
            amount_crypto_initial=amount_crypto,
            amount_crypto_remaining=amount_crypto, # Изначально равен начальному
            price_per_unit=price_per_unit,
            status='open'
        )
        db.session.add(new_order)
        db.session.flush() # Чтобы получить new_order.id для matching

        # Пытаемся сразу сопоставить ордер
        match_found = find_and_execute_match(new_order)

        db.session.commit() # Коммитим новый ордер и возможные сделки

        if match_found:
             flash(f'Ордер размещен и частично или полностью исполнен!', 'success')
        else:
            flash(f'Ордер {new_order.id} успешно размещен.', 'success')

    except ValueError as e:
        db.session.rollback()
        flash(f"Ошибка размещения ордера: {e}", 'error')
    except Exception as e:
        db.session.rollback()
        flash(f"Непредвиденная ошибка: {e}", 'error')
        app.logger.error(f"P2P place order error: {e}")

    return redirect(url_for('p2p_page'))


@app.route('/p2p/cancel_order/<int:order_id>', methods=['POST'])
def cancel_p2p_order(order_id):
    if 'user_id' not in session: return jsonify({"error": "Not logged in"}), 401
    user_id = session['user_id']

    order_to_cancel = P2POrder.query.filter_by(id=order_id, user_id=user_id).first()

    if not order_to_cancel:
        flash("Ордер не найден или не принадлежит вам.", 'error')
    elif order_to_cancel.status != 'open':
        flash("Нельзя отменить уже исполненный или отмененный ордер.", 'error')
    else:
        try:
            order_to_cancel.status = 'cancelled'
            db.session.commit()
            flash(f"Ордер {order_id} успешно отменен.", 'success')
        except Exception as e:
            db.session.rollback()
            flash(f"Ошибка отмены ордера: {e}", 'error')
            app.logger.error(f"P2P cancel order error: {e}")

    return redirect(url_for('p2p_page'))


# --- Логика Сопоставления (Matching Engine) ---
def find_and_execute_match(new_order: P2POrder):
    """Ищет и исполняет сделки для нового ордера."""
    match_found = False
    # Используем with_for_update для блокировки строк во время поиска и исполнения
    # В SQLite это может работать не так надежно, как в PostgreSQL/MySQL

    with db.session.begin_nested(): # Для частичного rollback при ошибке в цикле
        while new_order.amount_crypto_remaining_decimal > Decimal('0') and new_order.status == 'open':
            compatible_order = None
            if new_order.order_type == 'buy':
                # Ищем лучшие SELL ордера (самая низкая цена) от ДРУГИХ пользователей
                compatible_order = P2POrder.query\
                    .filter(P2POrder.crypto_currency == new_order.crypto_currency,
                            P2POrder.order_type == 'sell',
                            P2POrder.status == 'open',
                            P2POrder.user_id != new_order.user_id, # От другого пользователя
                            P2POrder.price_per_unit <= new_order.price_per_unit) \
                    .order_by(P2POrder.price_per_unit.asc(), P2POrder.created_at.asc()) \
                    .with_for_update(skip_locked=True).first() # Блокируем найденный ордер
            else: # new_order.order_type == 'sell':
                # Ищем лучшие BUY ордера (самая высокая цена) от ДРУГИХ пользователей
                compatible_order = P2POrder.query\
                    .filter(P2POrder.crypto_currency == new_order.crypto_currency,
                            P2POrder.order_type == 'buy',
                            P2POrder.status == 'open',
                            P2POrder.user_id != new_order.user_id, # От другого пользователя
                            P2POrder.price_per_unit >= new_order.price_per_unit) \
                    .order_by(P2POrder.price_per_unit.desc(), P2POrder.created_at.asc()) \
                    .with_for_update(skip_locked=True).first() # Блокируем найденный ордер

            if not compatible_order:
                break # Нет подходящих встречных ордеров

            # Определяем цену и количество сделки
            trade_price = compatible_order.price_per_unit_decimal # Цена встречного ордера
            trade_amount_crypto = min(new_order.amount_crypto_remaining_decimal,
                                     compatible_order.amount_crypto_remaining_decimal)

            if trade_amount_crypto <= Decimal('0'): # На всякий случай
                 print(f"Warning: Trade amount is zero or negative for orders {new_order.id} and {compatible_order.id}")
                 compatible_order.status = 'filled' # Считаем этот встречный ордер исполненным (он пустой)
                 db.session.add(compatible_order)
                 continue # Пробуем найти следующий

            trade_amount_fiat = quantize_usd(trade_amount_crypto * trade_price)

            # Определяем покупателя и продавца
            buyer_order = new_order if new_order.order_type == 'buy' else compatible_order
            seller_order = compatible_order if new_order.order_type == 'buy' else new_order
            buyer_id = buyer_order.user_id
            seller_id = seller_order.user_id

            # --- Перепроверка балансов перед исполнением ---
            buyer = User.query.get(buyer_id)
            seller = User.query.get(seller_id)
            if not buyer or not seller:
                 app.logger.error(f"Match Error: Buyer or Seller not found for orders {buyer_order.id}/{seller_order.id}")
                 # Возможно, стоит отменить оба ордера или только встречный
                 compatible_order.status = 'cancelled' # Отменяем встречный, т.к. юзера нет
                 db.session.add(compatible_order)
                 continue # Ищем следующий

            # Важно: эти проверки не учитывают другие ОДНОВРЕМЕННЫЕ транзакции без блокировок на уровне баланса
            if buyer.get_available_balance('USD') < trade_amount_fiat:
                 app.logger.warning(f"Match Skipped: Insufficient USD for buyer {buyer_id} on order {buyer_order.id}")
                 # Пропускаем этот compatible_order, ищем следующий (или можно отменить ордер покупателя)
                 # compatible_order.status = 'open' # Оставляем его открытым для других
                 continue
            if seller.get_available_balance(new_order.crypto_currency) < trade_amount_crypto:
                 app.logger.warning(f"Match Skipped: Insufficient {new_order.crypto_currency} for seller {seller_id} on order {seller_order.id}")
                 # Пропускаем этот compatible_order (или можно отменить ордер продавца)
                 continue


            # --- Исполнение сделки ---
            try:
                # 1. Обновляем балансы
                update_balance(buyer_id, 'USD', -trade_amount_fiat)
                update_balance(buyer_id, new_order.crypto_currency, trade_amount_crypto)
                update_balance(seller_id, new_order.crypto_currency, -trade_amount_crypto)
                update_balance(seller_id, 'USD', trade_amount_fiat)

                # 2. Обновляем ордера
                buyer_order.amount_crypto_remaining = buyer_order.amount_crypto_remaining_decimal - trade_amount_crypto
                seller_order.amount_crypto_remaining = seller_order.amount_crypto_remaining_decimal - trade_amount_crypto

                if buyer_order.amount_crypto_remaining_decimal <= Decimal('0'):
                    buyer_order.status = 'filled'
                # else: buyer_order.status = 'partially_filled' # Пока не используем

                if seller_order.amount_crypto_remaining_decimal <= Decimal('0'):
                    seller_order.status = 'filled'
                # else: seller_order.status = 'partially_filled'

                # 3. Записываем сделку в историю P2P
                p2p_trade = P2PTrade(
                    buy_order_id=buyer_order.id,
                    sell_order_id=seller_order.id,
                    buyer_id=buyer_id,
                    seller_id=seller_id,
                    crypto_currency=new_order.crypto_currency,
                    fiat_currency='USD',
                    amount_crypto=trade_amount_crypto,
                    price_per_unit=trade_price
                )
                db.session.add(p2p_trade)
                db.session.add(buyer_order)
                db.session.add(seller_order)

                match_found = True
                app.logger.info(f"P2P Match Executed: Order {new_order.id} matched with {compatible_order.id}. Amount: {trade_amount_crypto} {new_order.crypto_currency} @ ${trade_price}")

            except Exception as e:
                # Откат изменений в этом конкретном матче, но не всего процесса
                 db.session.rollback() # Откатывает только с begin_nested()
                 app.logger.error(f"Error executing match between {new_order.id} and {compatible_order.id}: {e}")
                 # Можно пометить compatible_order как проблемный или просто пропустить
                 break # Прерываем цикл мачинга для этого new_order при ошибке

    return match_found


# --- Остальные маршруты (/history, /logout, init-db, context_processor) ---
# Маршрут /history теперь должен показывать только РЫНОЧНУЮ историю
@app.route('/history')
def history():
    if 'user_id' not in session:
        flash('Пожалуйста, войдите.', 'error')
        return redirect(url_for('login'))
    user_id = session['user_id']
    user_transactions = Transaction.query.filter_by(user_id=user_id)\
                                     .order_by(Transaction.timestamp.desc()).all()
    return render_template('history.html', transactions=user_transactions, title="История Рыночных Сделок")

# Добавим новый обработчик init-db или нужно будет удалить БД и создать заново
@app.cli.command('reset-db')
def reset_db():
    """Удаляет и создает заново таблицы базы данных."""
    if input("Это удалит все данные! Вы уверены? (yes/no): ").lower() == 'yes':
        with app.app_context():
            db.drop_all()
            db.create_all()
        print("База данных пересоздана!")
    else:
        print("Отменено.")


def format_decimal_filter(value, precision=8):
    if value is None:
        return "0.0" # Или другое значение по умолчанию
    try:
        # Убедимся, что это Decimal перед форматированием
        d_value = Decimal(value)
        # Создаем форматную строку типа '%.8f'
        fmt_string = f"%.{precision}f"
        return fmt_string % d_value
    except:
        return str(value) # Возвращаем как есть, если не можем конвертировать

# --- Запуск ---
if __name__ == '__main__':
    # ВНИМАНИЕ: Перед первым запуском с новыми моделями,
    # удалите старый файл exchange.db и выполните:
    # flask reset-db (или flask init-db, если его не было)
    with app.app_context():
        db.create_all() # Убедимся, что все таблицы созданы, если init-db не вызывался
    app.jinja_env.filters['format_decimal'] = format_decimal_filter
    app.run(debug=True, use_reloader=False) # use_reloader=False иногда помогает при сложных сессиях БД в debug
