import psycopg2
import requests
import xml.etree.ElementTree as ET


# === CONFIGURATION ===
DB_CONFIG = {
    "dbname": "",
    "user": "",
    "password": "",
    "host": "",
    "port": 6543,
}


API_URL_TEMPLATE = "http://southflorals.dyndns.org:15080/cgi-bin/masapi.cgi?q=account&account_id={customer_id}"

def parse_customer_xml(xml_string):
    root = ET.fromstring(xml_string)
    customer = {}

    for child in root:
        customer[child.tag] = child.text or ""
    
    return customer

def save_customer(customer_data):
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()

    columns = customer_data.keys()
    values = [customer_data[col] for col in columns]

    placeholders = ', '.join(['%s'] * len(values))
    columns_str = ', '.join(columns)
    updates = ', '.join([f"{col} = EXCLUDED.{col}" for col in columns if col != "account_id"])

    sql = f"""
        INSERT INTO mas_customers ({columns_str})
        VALUES ({placeholders})
        ON CONFLICT (account_id) DO UPDATE SET {updates};
    """

    cur.execute(sql, values)
    conn.commit()
    cur.close()
    conn.close()


def fetch_customer_ids():
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()

    cur.execute("SELECT DISTINCT customer_id FROM mas_orders WHERE customer_id IS NOT NULL;")
    rows = cur.fetchall()

    cur.close()
    conn.close()

    return [row[0] for row in rows]



def fetch_customer_details(customer_ids):
    for customer_id in customer_ids:
        url = API_URL_TEMPLATE.format(customer_id=customer_id)
        try:
            print(f"Fetching: {url}")
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            customer_data = parse_customer_xml(response.text)
            save_customer(customer_data)
            print(f"✅ Saved customer {customer_id}")
        except Exception as e:
            print(f"❌ Error fetching {customer_id}: {e}")


# === MAIN ===
if __name__ == "__main__":
    customer_ids = fetch_customer_ids()
    fetch_customer_details(customer_ids)

