# MySnow Design Document ❄️

## 1. Overview
**MySnow** is a professional-grade weather visualization platform tailored for winter sports enthusiasts. It focuses on high-precision snowfall physics, multi-model data blending, and advanced data density.

## 2. Goals
- Provide localized, high-resolution snow forecasts with professional accuracy.
- Implement a **Multi-Algorithm Physics Engine** for Snow-to-Liquid Ratio (SLR) estimation.
- Deploy a **Thermodynamic Snow Level Engine** accounting for atmospheric cooling.
- Offer a **triple-layer synchronized view** of weather telemetry across 16 days.
- Combine HRRR (high-res short-term) and ECMWF (long-range) models into a unified data stream.

## 3. Architecture

### 3.1. Technical Stack
- **Language**: TypeScript (Strict Mode).
- **Build System**: Vite.
- **Framework**: Vanilla (Optimized for performance).
- **Styling**: Pure CSS with Glassmorphism and responsive design.
- **Deployment**: GitHub Pages.

### 3.2. Physics Engines

### 3.2. Physics Engines

#### 3.2.1. Precipitation Phase Determination
Before accumulation is calculated, a phase check is performed in `src/slr.ts`:
- **Wet-Bulb Criterion**: `If (Tw > 1.5°C) → Rain (No Snowfall)`.
- **Soil Melting**: If `T_soil > 1.0°C`, the SLR is linearly penalized: `SLR = SLR * max(0.2, 1 - (T_soil / 5))`.

#### 3.2.2. Advanced Kinematic Physical Snow Model & Densification (Default)
The entire `sierra_custom` engine was upgraded using an 8-step thermodynamic profile integration, driven by the extraction of specific atmospheric variables directly from the 1000hPa through 300hPa layers. 

**Step 1: Isolate the Snow Production Zone (SPZ)**
- A layer $i$ is active if Relative Humidity $\ge 90\%$ AND Vertical Velocity ($\omega$) $< 0$.

**Step 2: Calculate Kinematic Base Ratio ($SLR_{base}$)**
- Assign a Layer Snow Ratio ($LSR_i$) based on Cobb-Waldstreicher mapping (interpolated): 
  (-20°C: 18.0 | -18°C: 23.0 | -12°C: 17.5 | -4°C: 8.5 | 0°C: 3.0).
- Kinematic Weight: $W_i = (|\omega_i| \cdot \Delta Z_i) / \sum(|\omega| \cdot \Delta Z)$.
- Baseline SLR: $SLR_{base} = \sum (W_i \cdot LSR_i)$.

**Step 3: Dynamic Riming Penalty ($F_{rime}$)**
- In active layers between $-10^\circ\text{C}$ and $0^\circ\text{C}$, find $\omega_{warm\_max}$.
- $F_{rime} = 1.0 - (0.50 \cdot (|\omega_{warm\_max}| / |\omega_{max}|))$.

**Step 4: Wind Compaction Penalty ($F_{wind}$)**
- Peak wind $U_{max}$ derived from 700hPa and 10m surface winds.
- If $U_{max} > 8$ m/s, $F_{wind} = 1.0 - 0.15 \cdot \ln(U_{max} - 7)$.

**Step 5: Boundary Layer Melt Penalty ($F_{melt}$)**
- Using Wet-Bulb Temp ($T_w$): $F_{melt} = \exp(-0.5 \cdot \max(0, T_w))$ if $T_{2m} > 0^\circ\text{C}$.
- Hard boundary constraint: SLR halves if average surface temperature $\ge 1.6^\circ\text{C}$.

**Step 6: Final Kinematic SLR**
- $SLR_{Final} = SLR_{base} \times F_{rime} \times F_{wind} \times F_{melt}$ (Capped between 1 and 30).

**Step 7: Snow Accumulation (Hourly)**
- $Snowfall_{hourly} = Precipitation \times SLR_{Final}$.

**Step 8: Multilayer Densification Model**
Instead of simple summation, the physics engine maintains a continuous state of discrete hourly snow layers over the entire storm duration:
- **Creation**: Hourly layer $k$ initializes with $SWE_k$ and $\rho_{init} = 1000 / SLR_{Final}$.
- **Overburden & Settling**: Older layers pack down as new layers fall on top. Density increases: $\rho_k += \rho_k \cdot (C_1 \cdot T_{factor} + C_2 \cdot SWE_{above})$.
- **Final Depth Validation**: $HS = \sum (SWE_k / 10 \cdot (1000 / \rho_k))$.

#### 3.2.3. Thermodynamic Snow Level Engine
Located in `src/data.ts`, `calcSnowLevel` implements a physics-based approach:
- **Baseline**: Native Freezing Level Height (FL).
- **Evaporative Offset**: `If RH < 100: Offset += (100 - RH) * 2.5m`.
- **Diabatic Offset**: `If P > 1.0mm/hr: Offset += min(P * 15, 200)m`.
- **Final Result**: `SnowLevel = max(0, FL - Offset)`.

#### 3.2.4. Lapse Rate Fallback
If the model omits the 0°C isotherm (e.g., ECMWF), we derive the Freezing Level (FL):
- `FL = Elevation + (T_2m * (1000 / 6.5))` 
- (Based on the ICAO Standard Environmental Lapse Rate of $ 6.5^\circ\text{C} / \text{km}$).

### 3.3. Data Pipeline
1.  **API Fetching (`src/api.ts`)**:
    - Calls [Open-Meteo API](https://open-meteo.com/).
    - Fetches **HRRR (0-48h)** for high-resolution short-term data (3km).
    - Fetches **ECMWF IFS (48h - 16 days)** for long-range planning (9km).
2.  **Blending & Grouping (`src/data.ts`)**:
    - Prioritizes HRRR for the first 48 hours.
    - Synchronizes timestamps and fills missing data gaps.
    - Groups hourly telemetry into daily buckets and 3-hourly analytical windows.

## 4. UI/UX Design System
- **Segmented Control Toggles**: Multi-state UI for selecting physics algorithms and weather models.
- **Unified Header Data Bar**: High-density row merging location metadata with real-time conditions (Temp, Wind, Snow Rate).
- **Triple-Layer Synchronized Charts**: Linked horizontal scrollers for Snowfall, Temperature (SVG line), and Telemetry (dense grid).
- **Dynamic Snow Hue**: Card colors shift from Blue (Wet) to Purple (Powder) based on the day's average SLR.

## 5. Deployment & Development
- **Dev Server**: `npm run dev`
- **Build**: `npm run build`
- **Type Checking**: `npx tsc --noEmit`

## 6. Future Roadmap
- **UV Index Integration**: Completed.
- **Alert System**: Notifications for powder dump detection.
- **Map Integration**: Sierra-wide accumulation maps.
