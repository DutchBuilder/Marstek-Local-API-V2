"""Constants for Marstek Local API V2."""

DOMAIN = "marstek_local_api_v2"
CONF_PORT = "port"

# Entity references (dynamic sensors)
CONF_ELECTRICITY_PRICE_ENTITY = "electricity_price_entity"
CONF_GRID_POWER_ENTITY = "grid_power_entity"
CONF_MARKET_PRICE_ENTITY = "market_price_entity"

# Direct numeric values (entered in config flow)
CONF_ENERGY_TAX = "energy_tax"              # €/kWh excl. BTW
CONF_PROCUREMENT_FEE = "procurement_fee"    # €/kWh inkoopvergoeding
CONF_PLAN_HOURS = "plan_hours"              # aantal uren laden/ontladen
CONF_MIN_SPREAD = "min_spread"              # minimum prijsverschil €/kWh
CONF_MAX_CHARGE_WATTS = "max_charge_watts"        # W per batterij
CONF_MAX_DISCHARGE_WATTS = "max_discharge_watts"  # W per batterij

CONF_DOD = "dod_value"

DEFAULT_PORT = 30000
DEFAULT_SCAN_INTERVAL = 30  # seconds
DEFAULT_DOD = 88  # API default, range 30-88

# Plan defaults
DEFAULT_ENERGY_TAX = 0.0916
DEFAULT_PROCUREMENT_FEE = 0.0166
DEFAULT_PLAN_HOURS = 3
DEFAULT_MIN_SPREAD = 0.062
DEFAULT_MAX_CHARGE_WATTS = 800
DEFAULT_MAX_DISCHARGE_WATTS = 800

# Update tier multipliers (at 30s base interval)
UPDATE_TIER_FAST = 1       # every update   → 30s
UPDATE_TIER_MEDIUM = 10    # every 10 ticks → 300s
UPDATE_TIER_SLOW = 20      # every 20 ticks → 600s

# Timeout / retry
COMMAND_TIMEOUT = 15
DISCOVERY_TIMEOUT = 9
MAX_RETRIES = 3
COMMAND_BACKOFF_BASE = 1.5
COMMAND_BACKOFF_FACTOR = 2.0
COMMAND_BACKOFF_MAX = 12.0
COMMAND_BACKOFF_JITTER = 0.4
RETRY_DELAY = 2

# Stale data threshold
STALE_AFTER_MISSED = 3

# API method names (Rev 2.0)
METHOD_GET_DEVICE = "Marstek.GetDevice"
METHOD_WIFI_STATUS = "Wifi.GetStatus"
METHOD_BLE_STATUS = "BLE.GetStatus"
METHOD_BAT_STATUS = "Bat.GetStatus"
METHOD_PV_STATUS = "PV.GetStatus"
METHOD_ES_STATUS = "ES.GetStatus"
METHOD_ES_SET_MODE = "ES.SetMode"
METHOD_ES_GET_MODE = "ES.GetMode"
METHOD_EM_STATUS = "EM.GetStatus"
METHOD_DOD_SET = "DOD.SET"
METHOD_BLE_ADV = "Ble.Adv"
METHOD_LED_CTRL = "Led.Ctrl"

# Device model strings (as returned by Marstek.GetDevice)
DEVICE_MODEL_VENUS_C = "VenusC"
DEVICE_MODEL_VENUS_D = "VenusD"
DEVICE_MODEL_VENUS_E = "VenusE"
DEVICE_MODEL_VENUS_A = "VenusA"

# Models that support PV
MODELS_WITH_PV = {DEVICE_MODEL_VENUS_D, DEVICE_MODEL_VENUS_A}

# Operating modes
MODE_AUTO = "Auto"
MODE_AI = "AI"
MODE_MANUAL = "Manual"
MODE_PASSIVE = "Passive"
MODE_UPS = "Ups"

ALL_MODES = [MODE_AUTO, MODE_AI, MODE_MANUAL, MODE_PASSIVE, MODE_UPS]

# Battery / ES states
STATE_CHARGING = "charging"
STATE_DISCHARGING = "discharging"
STATE_IDLE = "idle"
STATE_CONFLICTING = "conflicting"

# CT / BLE connection states
CT_NOT_CONNECTED = 0
CT_CONNECTED = 1

# Data categories (for staleness tracking)
CATEGORY_FAST = "fast"        # bat + es + mode
CATEGORY_MEDIUM = "medium"    # em + pv
CATEGORY_SLOW = "slow"        # device + wifi + ble

# Config entry data keys
CONF_BLE_MAC = "ble_mac"
CONF_WIFI_MAC = "wifi_mac"
CONF_DEVICE_MODEL = "device_model"
CONF_FIRMWARE = "firmware"
CONF_DEVICE_NAME = "device_name"

# Meta key: which config entry "owns" the fleet-wide plan sensors
PLAN_SENSORS_ENTRY_KEY = "_plan_sensors_entry_id"

# Max schedule slots
MAX_SCHEDULE_SLOTS = 10

# Weekday bitmask (low 7 bits; bit 0 = Monday per API docs but API shows 0b0000_0001 = 1 = Monday)
WEEKDAY_ALL = 127
