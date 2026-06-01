import requests
import xml.etree.ElementTree as ET
import psycopg2
from psycopg2.extras import execute_batch


DB_CONFIG = {
    "dbname": "",
    "user": "",
    "password": "",
    "host": "",
    "port": 6543,
}


def fetch_orders_dynamic(bdate, edate):
    url = f"http://southflorals.dyndns.org:15080/cgi-bin/masapi.cgi?q=order&order_id=ALL&bdate={bdate}&edate={edate}&date_type=D&statusupdate=no"
    response = requests.get(url)
    response.raise_for_status()
    return response.text



def parse_orders(xml_data):
    root = ET.fromstring(xml_data)
    orders = []
    products = []

    for order_elem in root.findall("order"):
        order = {
            "order_id": order_elem.findtext("order_id"),
            "customer_id": order_elem.findtext("customer_id"),
            "order_type": order_elem.findtext("order_type"),
            "sale_type": order_elem.findtext("sale_type"),
            "order_date": order_elem.findtext("order_date"),
            "order_time": order_elem.findtext("order_time"),
            "delivery_date": order_elem.findtext("delivery_date"),
            "sold_name": order_elem.findtext("sold_name"),
            "sold_addr1": order_elem.findtext("sold_addr1"),
            "sold_addr2": order_elem.findtext("sold_addr2"),
            "sold_city": order_elem.findtext("sold_city"),
            "sold_state": order_elem.findtext("sold_state"),
            "sold_zip": order_elem.findtext("sold_zip"),
            "sold_phone": order_elem.findtext("sold_phone"),
            "sold_atel": order_elem.findtext("sold_atel"),
            "reference": order_elem.findtext("reference"),
            "recip_name": order_elem.findtext("recip_name"),
            "recip_attn": order_elem.findtext("recip_attn"),
            "recip_addr1": order_elem.findtext("recip_addr1"),
            "recip_addr2": order_elem.findtext("recip_addr2"),
            "recip_city": order_elem.findtext("recip_city"),
            "recip_state": order_elem.findtext("recip_state"),
            "recip_zip": order_elem.findtext("recip_zip"),
            "recip_phone": order_elem.findtext("recip_phone"),
            "delivery_type": order_elem.findtext("delivery_type"),
            "time_request": order_elem.findtext("time_request"),
            "special_instructions": order_elem.findtext("special_instructions"),
            "mdse_amount": order_elem.findtext("mdse_amount"),
            "delivery_amount": order_elem.findtext("delivery_amount"),
            "service_amount": order_elem.findtext("service_amount"),
            "tax_amount": order_elem.findtext("tax_amount"),
            "total_amount": order_elem.findtext("total_amount"),
            "discount_amount": order_elem.findtext("discount_amount"),
            "cc_tendered": order_elem.findtext("cc_tendered"),
            "order_status": order_elem.findtext("order_status"),
            "designer_status": order_elem.findtext("designer_status"),
            "designer": order_elem.findtext("designer"),
            "filled_date": order_elem.findtext("filled_date"),
            "filled_time": order_elem.findtext("filled_time"),
            "delivery_status": order_elem.findtext("delivery_status"),
            "delivery_code": order_elem.findtext("delivery_code"),
            "delivered_date": order_elem.findtext("delivered_date"),
            "delivered_time": order_elem.findtext("delivered_time"),
            "driver": order_elem.findtext("driver"),
            "wire_status": order_elem.findtext("wire_status"),
            "card_message": order_elem.findtext("card_message"),
        }
        orders.append(order)

        # Parse products if they exist
        products_elem = order_elem.find("products")
        if products_elem is not None:
            for product_elem in products_elem.findall("product"):
                product = {
                    "order_id": order_elem.findtext("order_id"),
                    "product_id": product_elem.findtext("product_id"),
                    "description": product_elem.findtext("description"),
                    "qty": product_elem.findtext("qty"),
                    "unit_price": product_elem.findtext("unit_price"),
                    "extended_price": product_elem.findtext("extended_price"),
                }
                products.append(product)
    
    return orders, products

def save_orders(orders):
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()

    sql = """
    INSERT INTO mas_orders (
        order_id, customer_id, order_type, sale_type, order_date,
        order_time, delivery_date, sold_name, sold_addr1, sold_addr2,
        sold_city, sold_state, sold_zip, sold_phone, sold_atel,
        reference, recip_name, recip_attn, recip_addr1, recip_addr2,
        recip_city, recip_state, recip_zip, recip_phone, delivery_type,
        time_request, special_instructions, mdse_amount, delivery_amount,
        service_amount, tax_amount, total_amount, discount_amount, cc_tendered,
        order_status, designer_status, designer, filled_date, filled_time,
        delivery_status, delivery_code, delivered_date, delivered_time,
        driver, wire_status, card_message
    )
    VALUES (
        %(order_id)s, %(customer_id)s, %(order_type)s, %(sale_type)s, %(order_date)s,
        %(order_time)s, %(delivery_date)s, %(sold_name)s, %(sold_addr1)s, %(sold_addr2)s,
        %(sold_city)s, %(sold_state)s, %(sold_zip)s, %(sold_phone)s, %(sold_atel)s,
        %(reference)s, %(recip_name)s, %(recip_attn)s, %(recip_addr1)s, %(recip_addr2)s,
        %(recip_city)s, %(recip_state)s, %(recip_zip)s, %(recip_phone)s, %(delivery_type)s,
        %(time_request)s, %(special_instructions)s, %(mdse_amount)s, %(delivery_amount)s,
        %(service_amount)s, %(tax_amount)s, %(total_amount)s, %(discount_amount)s, %(cc_tendered)s,
        %(order_status)s, %(designer_status)s, %(designer)s, %(filled_date)s, %(filled_time)s,
        %(delivery_status)s, %(delivery_code)s, %(delivered_date)s, %(delivered_time)s,
        %(driver)s, %(wire_status)s, %(card_message)s
    )
    ON CONFLICT (order_id) DO NOTHING;
    """

    execute_batch(cur, sql, orders)
    conn.commit()
    cur.close()
    conn.close()

def save_products(products):
    if not products:
        return

    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()

    sql = """
    INSERT INTO mas_products (
        order_id, product_id, description, qty, unit_price, extended_price
    )
    VALUES (
        %(order_id)s, %(product_id)s, %(description)s, %(qty)s, %(unit_price)s, %(extended_price)s
    )
    """

    # Make sure qty, unit_price, extended_price are numbers
    for product in products:
        product["qty"] = int(product["qty"]) if product["qty"] else None
        product["unit_price"] = float(product["unit_price"]) if product["unit_price"] else None
        product["extended_price"] = float(product["extended_price"]) if product["extended_price"] else None

    execute_batch(cur, sql, products)
    conn.commit()
    cur.close()
    conn.close()


def run_backup_for_manual_dates(dates_list):
    for date in dates_list:
        bdate = date
        edate = date
        
        print(f"Running backup for {bdate}...")

        try:

            xml_data = fetch_orders_dynamic(bdate, edate)
            orders, products = parse_orders(xml_data)

            print(f"Found {len(orders)} orders.")
            print(f"Found {len(products)} products.")

            save_orders(orders)
            save_products(products)

            print(f"Backup successful for {bdate} ✅\n")

        except Exception as e:
            print(f"Error backing up {bdate}: {e}")


dates_array = []

# === MAIN FUNCTION ===
def main():
    run_backup_for_manual_dates(dates_array)

if __name__ == "__main__":
    main()


