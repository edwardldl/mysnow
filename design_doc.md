# MySnow Design Document ❄️

## 1. Overview
**MySnow** is a premium weather visualization platform specifically tailored for winter sports enthusiasts in California. It emphasizes high-resolution snowfall data, snow quality (SLR), and detailed atmospheric telemetry to help users plan "powder days" with professional accuracy.

## 2. Goals
- Provide localized, high-resolution snow forecasts for specific mountain coordinates.
- Visualize snow quality using the **Kuchera Method** for dynamic SLR calculation.
- Offer a **triple-layer synchronized view** of weather metrics (Snow, Temperature, Wind, etc.).
- Combine multiple meteorological models (HRRR and ECMWF) for both short-term precision and long-term outlook.

## 3. Architecture

### 3.1. Frontend Stack
- **Languages**: HTML5, CSS3, JavaScript (ES6 Modules).
- **Framework**: None (Vanilla implementation for maximum performance and control).
- **Icons/Fonts**: Google Fonts (Inter).
- **Visuals**: SVG for line graphs, Glassmorphism for UI elements.
- **Data Persistence**: `localStorage` for user-defined stashes.

### 3.2. Data Pipeline
The application uses a "Blend & Group" strategy:
1.  **API Fetching (`api.js`)**:
    - Calls [Open-Meteo API](https://open-meteo.com/).
    - Fetches **HRRR (0-48h)** for high-resolution short-term data (3km resolution).
    - Fetches **ECMWF IFS (0-15 days)** for global coverage and long-range planning.
2.  **Model Blending (`data.js`)**:
    - Prioritizes HRRR data for overlapping timestamps (the first 48 hours).
    - Falls back to ECMWF for extended forecasts.
    - Logic: `if (hrrrDataAvailable) use HRRR else use ECMWF`.
3.  **Data Processing (`data.js`)**:
    - **Dynamic SLR Calculation**: Using the **Kuchera Method** quadratic formula: `SLR = 12.0 - (0.5 * T) + (0.06 * T²)`.
    - **Wind Adjustment**: Significant wind gusts ( > 50 km/h) cap the SLR to account for mechanical fracturing of snow crystals.
    - **Grouping**: Hourly data is grouped into daily objects and 3-hourly "windows" for summarized views.

### 3.3. UI/UX Design System
- **Layout**: Single-page application (SPA) with Glassmorphism aesthetics (`backdrop-filter: blur()`).
- **Triple-Layer Synchronized Charts**: Three horizontal scroll containers linked by a custom sync-scroll engine:
    1.  **Precipitation Activity**: Bar chart showing liquid equivalent with snowfall labels.
    2.  **Temperature Trend**: SVG line graph with dynamic scaling and colorful linear gradients (Red for hot, Blue for cold).
    3.  **Weather Telemetry**: A dense grid of metrics including Feels Like, Snow Level, Precip %, Humidity, and Cloud Cover.
- **Ski Day Markers**: Semi-transparent blue vertical regions highlighting the 09:00 - 16:00 window for optimal skiing hours.

## 4. Key Components (`render.js`)
- **Location Switcher**: Interactive pills to switch between default and custom stashes.
- **Day Summary Cards**: Scrollable list with "Big Storm" highlights for totals over 20cm, involving dynamic hue shifting based on snow quality.
- **Sync-Scroll Engine**: A custom event handler synchronizes `scrollLeft` across all three charts, ensuring time-axis alignment.
- **Continuous SLR Legend**: A gradient legend showing the transition from Wet (5:1) to Dry (>20:1) snow quality.

## 5. Metadata and Algorithms
### 5.1. Snow-to-Liquid Ratio (SLR) Categories
- **Rain**: Temperature > 2°C or snowfall = 0 while precip > 0.
- **Wet (Sierra Cement)**: SLR < 10:1.
- **Standard**: SLR 10:1 - 15:1.
- **Powder (Cold Smoke)**: SLR > 15:1.

### 5.2. Visual Color Mapping
- **Snow Bars**: Dynamic HSL gradient from 180° (Light Blue) through 210° (Blue) to 30° (Gold) based on SLR.
- **Temperature SVG**: Dynamic range scaling centered on the day's median temperature with a minimum 5°C window to prevent over-scaling of minor fluctuations.

## 6. Future Roadmap
- **UV Index Integration**: Adding solar radiation data to the telemetry layer.
- **Map Integration**: Visualizing snowfall accumulation across the Sierra Nevada range.
- **Alert System**: Browser notifications for high-confidence powder alerts.
- **Historical Comparison**: Benchmarking current totals against historical storm averages.
