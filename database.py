import asyncpg
from typing import Optional, List, Dict
import logging

logger = logging.getLogger(__name__)


class Database:
    def __init__(self, database_url: str):
        self.database_url = database_url
        self.pool = None

    async def connect(self):
        self.pool = await asyncpg.create_pool(self.database_url, ssl='require')
        await self.create_tables()
        logger.info("✅ Ma'lumotlar bazasi ulandi")

    async def create_tables(self):
        async with self.pool.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id BIGINT PRIMARY KEY,
                    full_name TEXT NOT NULL,
                    username TEXT,
                    phone TEXT,
                    total_debt BIGINT DEFAULT 0,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS products (
                    id SERIAL PRIMARY KEY,
                    name TEXT NOT NULL,
                    price BIGINT NOT NULL,
                    photo_id TEXT,
                    is_active BOOLEAN DEFAULT TRUE,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS orders (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT REFERENCES users(id),
                    total_amount BIGINT NOT NULL,
                    debt_amount BIGINT DEFAULT 0,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS order_items (
                    id SERIAL PRIMARY KEY,
                    order_id INTEGER REFERENCES orders(id),
                    product_id INTEGER REFERENCES products(id),
                    product_name TEXT NOT NULL,
                    price BIGINT NOT NULL,
                    quantity INTEGER DEFAULT 1
                )
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS cart (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT REFERENCES users(id),
                    product_id INTEGER REFERENCES products(id),
                    quantity INTEGER DEFAULT 1,
                    UNIQUE(user_id, product_id)
                )
            """)

    # --- USER ---
    async def get_user(self, user_id: int) -> Optional[Dict]:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM users WHERE id = $1", user_id)
            return dict(row) if row else None

    async def create_user(self, user_id: int, full_name: str, username: str = None):
        async with self.pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO users (id, full_name, username) VALUES ($1, $2, $3) ON CONFLICT DO NOTHING",
                user_id, full_name, username
            )

    async def update_user_phone(self, user_id: int, phone: str):
        async with self.pool.acquire() as conn:
            await conn.execute("UPDATE users SET phone = $1 WHERE id = $2", phone, user_id)

    async def get_all_users(self) -> List[Dict]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("SELECT * FROM users ORDER BY created_at DESC")
            return [dict(r) for r in rows]

    async def get_all_debtors(self) -> List[Dict]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM users WHERE total_debt > 0 ORDER BY total_debt DESC"
            )
            return [dict(r) for r in rows]

    async def add_debt(self, user_id: int, amount: int):
        async with self.pool.acquire() as conn:
            await conn.execute(
                "UPDATE users SET total_debt = total_debt + $1 WHERE id = $2",
                amount, user_id
            )

    async def reduce_debt(self, user_id: int, amount: int):
        async with self.pool.acquire() as conn:
            await conn.execute(
                "UPDATE users SET total_debt = GREATEST(0, total_debt - $1) WHERE id = $2",
                amount, user_id
            )

    # --- PRODUCTS ---
    async def get_all_products(self) -> List[Dict]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM products WHERE is_active = TRUE ORDER BY created_at DESC"
            )
            return [dict(r) for r in rows]

    async def add_product(self, name: str, price: int, photo_id: str = None):
        async with self.pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO products (name, price, photo_id) VALUES ($1, $2, $3)",
                name, price, photo_id
            )

    async def delete_product(self, product_id: int):
        async with self.pool.acquire() as conn:
            await conn.execute(
                "UPDATE products SET is_active = FALSE WHERE id = $1", product_id
            )

    async def get_product(self, product_id: int) -> Optional[Dict]:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM products WHERE id = $1", product_id)
            return dict(row) if row else None

    # --- CART ---
    async def get_cart(self, user_id: int) -> List[Dict]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT c.id, c.quantity, p.id as product_id, p.name, p.price, p.photo_id
                FROM cart c
                JOIN products p ON c.product_id = p.id
                WHERE c.user_id = $1
            """, user_id)
            return [dict(r) for r in rows]

    async def add_to_cart(self, user_id: int, product_id: int):
        async with self.pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO cart (user_id, product_id, quantity)
                VALUES ($1, $2, 1)
                ON CONFLICT (user_id, product_id)
                DO UPDATE SET quantity = cart.quantity + 1
            """, user_id, product_id)

    async def dec_from_cart(self, user_id: int, product_id: int):
        """Savatdan 1 ta kamaytiradi, 0 bo'lsa o'chiradi"""
        async with self.pool.acquire() as conn:
            qty = await conn.fetchval(
                "SELECT quantity FROM cart WHERE user_id = $1 AND product_id = $2",
                user_id, product_id
            )
            if qty and qty > 1:
                await conn.execute(
                    "UPDATE cart SET quantity = quantity - 1 WHERE user_id = $1 AND product_id = $2",
                    user_id, product_id
                )
            else:
                await conn.execute(
                    "DELETE FROM cart WHERE user_id = $1 AND product_id = $2",
                    user_id, product_id
                )

    async def clear_cart(self, user_id: int):
        async with self.pool.acquire() as conn:
            await conn.execute("DELETE FROM cart WHERE user_id = $1", user_id)

    # --- ORDERS ---
    async def create_order(self, user_id: int, cart_items: List[Dict], total_amount: int) -> int:
        async with self.pool.acquire() as conn:
            order_id = await conn.fetchval("""
                INSERT INTO orders (user_id, total_amount, debt_amount)
                VALUES ($1, $2, $2) RETURNING id
            """, user_id, total_amount)

            for item in cart_items:
                await conn.execute("""
                    INSERT INTO order_items (order_id, product_id, product_name, price, quantity)
                    VALUES ($1, $2, $3, $4, $5)
                """, order_id, item['product_id'], item['name'], item['price'], item['quantity'])

            # Qarzni oshirish
            await conn.execute(
                "UPDATE users SET total_debt = total_debt + $1 WHERE id = $2",
                total_amount, user_id
            )

            return order_id

    async def get_user_orders(self, user_id: int) -> List[Dict]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM orders WHERE user_id = $1 ORDER BY created_at DESC LIMIT 20",
                user_id
            )
            return [dict(r) for r in rows]

    async def get_all_orders(self) -> List[Dict]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT o.*, u.full_name FROM orders o
                JOIN users u ON o.user_id = u.id
                ORDER BY o.created_at DESC LIMIT 30
            """)
            return [dict(r) for r in rows]
