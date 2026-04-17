# Marstek Local API V2 – Home Assistant Integration

Custom HACS integration for Marstek home battery systems (Venus E3, Venus A/D) using the local UDP JSON-RPC API (Rev 2.0).

---

## Features

- **Auto-discovery** of Marstek devices on your local network (UDP broadcast)
- **Per-battery sensors**: SOC, temperature, capacity, grid/PV/battery power, operating mode, CT phase powers, cumulative energy
- **Per-battery computed sensors**: beschikbare kWh, charge/discharge power, kosten- en opbrengstrate (€/h), accumulated kosten/opbrengst (€, persists across restarts)
- **Per-battery controls**: DOD slider, LED switch, BLE advertising switch, mode buttons (Auto / AI / UPS)
- **Fleet-wide sensors**: total rated/remaining capacity, beschikbare kWh totaal, charge/discharge power totaal, solar/grid share %, stroomprijs totaal (all-in), Marstek plan vandaag/morgen
- **Fleet-wide binary sensors**: Marstek moet nu laden, Marstek moet nu ontladen
- **Tiered polling**: fast (30 s), medium (5 min), slow (10 min) to reduce network load

---

## Installation via HACS

1. In HACS → **Integrations** → three-dot menu → **Custom repositories**
2. Add this repository URL, category: **Integration**
3. Install **Marstek Local API V2**
4. Restart Home Assistant
5. Go to **Settings → Devices & Services → Add Integration** → search for **Marstek Local API V2**

---

## Setup

### Auto-discovery
The integration broadcasts a UDP packet on port 30000 and lists all responding Marstek devices.  
Select one or more devices, give them a name, and configure the options.

### Manual setup
If auto-discovery fails (e.g. the device is on a different subnet), choose **Manual** and enter:

| Field | Description | Default |
|-------|-------------|---------|
| IP address | Local IP of the battery | — |
| UDP port | UDP port the battery listens on | `30000` |

---

## Configuration options

These can be changed at any time via **Settings → Devices & Services → Marstek → Configure**.

| Option | Description | Default |
|--------|-------------|---------|
| Scan interval | How often to poll the battery (seconds) | `30` |
| Electricity price sensor (€/kWh) | All-in price entity for cost/revenue sensors | — |
| Grid import power sensor (W) | P1 meter active power entity (positive = import) | — |
| Market price sensor | Entity with `prices` attribute (Nordpool / EPEX) | — |
| Energy tax (€/kWh) | Energiebelasting excl. BTW, added to market price | `0.0916` |
| Procurement fee (€/kWh excl. BTW) | Inkoopvergoeding van leverancier bij teruglevering | `0.007` |
| Plan hours | Number of cheapest/most expensive hours to plan | `3` |
| Min spread (€/kWh) | Minimum price difference for WINSTGEVEND strategy | `0.062` |
| Max charge watts (W per battery) | Maximum charge power per battery | `800` |
| Max discharge watts (W per battery) | Maximum discharge power per battery | `800` |

---

## Entities per battery

### Sensors

| Entity | Description | Unit |
|--------|-------------|------|
| SOC | State of charge | % |
| Temperature | Battery temperature | °C |
| Rated capacity | Nominal battery capacity | Wh |
| Remaining capacity | Current remaining energy | Wh |
| Beschikbare kWh | Usable energy based on DOD | kWh |
| Grid power | Power exchange with grid (+ = export, − = import) | W |
| PV power | Total solar input power | W |
| Battery power | Charge/discharge power (+ = charge, − = discharge) | W |
| Charge power | Active charge power | W |
| Discharge power | Active discharge power | W |
| Operating mode | Current mode: Auto / AI / UPS / Manual / Passive | — |
| CT Phase L1/L2/L3 power | Individual phase grid power | W |
| Total charged energy | Cumulative energy charged | kWh |
| Total discharged energy | Cumulative energy discharged | kWh |
| Kosten rate | Cost rate at current electricity price | €/h |
| Opbrengst rate | Revenue rate at current electricity price | €/h |
| Batterijverbruik kosten totaal | Accumulated charging cost | € |
| Batterijverbruik opbrengst totaal | Accumulated discharge revenue | € |

*PV sensors (pv1–pv4) are automatically enabled for Venus A/D models.*

### Binary sensors

| Entity | Description |
|--------|-------------|
| Charging Allowed | Whether battery allows charging (charg_flag) |
| Discharging Allowed | Whether battery allows discharging (dischrg_flag) |
| Battery Charging | Battery is actively charging from grid |
| Battery Discharging | Battery is actively exporting to grid |
| CT Connected | CT clamp is connected and active |
| Bluetooth Connected | BLE connection active |

### Controls

| Entity | Type | Description | Range |
|--------|------|-------------|-------|
| DOD | Number | Depth of Discharge – minimum SOC the battery will discharge to | 30–88 % |
| LED | Switch | Turn the status LED on or off | on/off |
| BLE Advertising | Switch | Enable or disable Bluetooth advertising | on/off |
| Auto Mode | Button | Switch to **Auto** mode (grid-optimised, uses CT data) | — |
| AI Mode | Button | Switch to **AI** mode (learning-based optimisation) | — |
| UPS Mode | Button | Switch to **UPS** mode (keeps battery charged for backup) | — |

---

## Fleet-wide entities (Marstek Fleet device)

Created once for all configured batteries combined.  
Only created for the **first** configured entry (plan sensors entry).

### Sensors

| Entity | Description | Unit |
|--------|-------------|------|
| Total rated capacity | Sum of all battery nominal capacities | kWh |
| Total remaining capacity | Sum of all remaining capacities | kWh |
| Beschikbare kWh totaal | Total usable energy across all batteries | kWh |
| Charge power totaal | Combined active charge power | W |
| Discharge power totaal | Combined active discharge power | W |
| HA Battery Power Total Signed | Net battery power (+ charge / − discharge) | W |
| Solar share % | Percentage of power from solar | % |
| Grid share % | Percentage of power from grid | % |
| Stroomprijs totaal | All-in electricity price (market + tax + fee) × BTW | €/kWh |
| Marstek plan vandaag | Charge/discharge plan for today (JSON attributes) | — |
| Marstek plan morgen | Charge/discharge plan for tomorrow (JSON attributes) | — |

### Binary sensors

| Entity | Description |
|--------|-------------|
| Marstek moet nu laden | `true` when the current hour is a planned charge slot |
| Marstek moet nu ontladen | `true` when the current hour is a planned discharge slot |

---

## Plan attributes

The **Marstek plan vandaag** and **Marstek plan morgen** sensors expose a rich set of attributes usable in automations:

| Attribute | Description |
|-----------|-------------|
| `strategy` | `WINSTGEVEND` (profitable) or `EIGEN CONSUMPTIE` (self-consumption) |
| `spread_ok` | Whether the price spread exceeds `min_spread` |
| `min_pair_spread` | Lowest spread between cheapest and most expensive hour pair |
| `charge_hours` | Number of planned charge hours |
| `discharge_hours` | Number of planned discharge hours |
| `charge_watts_per_bat` | Charge power per battery (W) |
| `discharge_watts_per_bat` | Discharge power per battery (W) |
| `charge_watts_total` | Total combined charge power (W) |
| `discharge_watts_total` | Total combined discharge power (W) |
| `charge_kwh` | Estimated energy charged (kWh) |
| `discharge_kwh` | Estimated energy discharged (kWh) |
| `buy_cost` | Estimated cost of charging (€) |
| `save_revenue` | Estimated revenue from discharging (€) |
| `profit` | Estimated net profit (€) |
| `charge_slots` | List of `{start, end, price}` dicts for charge hours |
| `discharge_slots` | List of `{start, end, price}` dicts for discharge hours |

---

## Advanced: Manual and Passive mode

**Manual** and **Passive** modes are available via the UDP API but are not exposed as buttons.  
You can call them from a Home Assistant automation or script using the `button.press` or a custom `rest_command`.  
Alternatively, use the **Developer Tools → Actions** panel to call the integration's underlying client directly if you build your own automations around it.

### Manual mode parameters

| Parameter | Description |
|-----------|-------------|
| `time_num` | Schedule slot number |
| `start_time` | Start time (HH:MM) |
| `end_time` | End time (HH:MM) |
| `power` | Power in W (negative = charge, positive = discharge) |
| `week_set` | Bitmask for days of the week (default: 127 = every day) |
| `enable` | 1 = enable schedule, 0 = disable |

### Passive mode parameters

| Parameter | Description |
|-----------|-------------|
| `power` | Target grid power in W |
| `cd_time` | Cooldown time in seconds |

---

## Supported models

| Model | Status |
|-------|--------|
| Marstek Venus E3 | Tested ✓ |
| Marstek Venus A / Venus D | Tested ✓ (PV sensors auto-enabled) |
| Marstek Venus E2 | Under investigation – UDP port may differ |

---

## Requirements

- Home Assistant 2024.1.0 or newer
- Marstek battery reachable over local LAN (UDP, default port 30000)
- For plan sensors: Nordpool or EPEX integration providing an entity with a `prices` attribute
