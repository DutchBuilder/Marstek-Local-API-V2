# Marstek Local API V2 – Home Assistant Integration

Custom HACS integration for Marstek home battery systems (VenusE, Venus A/D) using the local UDP JSON-RPC API (Rev 2.0).

## Features

- **Auto-discovery** of Marstek devices on your local network (UDP broadcast)
- **Per-battery sensors**: SOC, temperature, capacity, grid/PV/battery power, operating mode, CT phase powers, cumulative energy
- **Per-battery computed sensors**: Batterijverbruik grid power, beschikbare kWh, charge/discharge power, kosten- en opbrengstrate (€/h), accumulated kosten/opbrengst (€, persists across restarts)
- **Fleet-wide sensors**: Total rated/remaining capacity, beschikbare kWh totaal, charge/discharge power totaal, HA Battery Power Total Signed, solar/grid share %, stroomprijs totaal (all-in), Marstek plan vandaag/morgen
- **Fleet-wide binary sensors**: Marstek moet nu laden, Marstek moet nu ontladen
- **Controls**: DOD (depth of discharge) number entity, LED switch, BLE advertising switch, mode buttons (Auto / AI / UPS)
- **Tiered polling**: fast (30s), medium (5 min), slow (10 min) to reduce network load

## Installation via HACS

1. In HACS → **Integrations** → three-dot menu → **Custom repositories**
2. Add this repository URL, category: **Integration**
3. Install **Marstek Local API V2**
4. Restart Home Assistant
5. Go to **Settings → Devices & Services → Add Integration** → search for **Marstek Local API V2**

## Configuration

After adding the integration you can configure optional entity references under **Options**:

| Option | Description | Example |
|--------|-------------|---------|
| Electricity price sensor (€/kWh) | All-in price for cost/revenue sensors | `sensor.stroomprijs_totaal` |
| Grid import power sensor (W) | P1 meter active power | `sensor.p1_meter_5c2faf0b38e4_active_power` |
| Market price sensor | Sensor with `prices` attribute (Nordpool/EPEX) | `sensor.current_electricity_market_price` |
| Energy tax sensor (€/kWh) | Energiebelasting excl. BTW | `input_number.energiebelasting_kwh` |
| Procurement fee sensor (€/kWh) | Inkoopvergoeding | `input_number.inkoopvergoeding_kwh` |
| Plan hours entity | Number of charge/discharge hours | `input_number.marstek_plan_hours` |
| Min spread entity (€/kWh) | Minimum price spread for WINSTGEVEND strategy | `input_number.marstek_min_spread` |

## Supported models

- Marstek VenusE 3.0 (tested)
- Marstek Venus A / Venus D (PV sensors pv1–pv4 enabled automatically)

## Requirements

- Home Assistant 2024.1.0 or newer
- Marstek battery reachable over local LAN (UDP port 30000)
