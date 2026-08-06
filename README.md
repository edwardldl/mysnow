# 🏔️ MySnow: Snow Forecasting

MySnow is a weather forecasting application for skiers, snowboarders, and mountain enthusiasts. It combines Open-Meteo forecasts with an explainable snowfall pipeline; forecasts should be treated as guidance, not a replacement for local observations or avalanche information.

![MySnow Preview](public/preview.png) *(Note: Add your own preview image here)*

## 🌟 Key Features

- **🎯 Multi-Resolution Modelling**: Seamlessly switch between regional high-resolution models (3km) and global premier models (9km).
- **❄️ Explainable snow physics**: Separates precipitation amount, frozen fraction, fresh-snow SLR, snowfall, and snowpack settling.
- **⛰️ Slope-aware products**: Separates reference fresh snowfall, natural slope deposition, opening-time new snow, and managed-piste depth.
- **📍 Real resort reference sites**: The built-in resort uses distinct base, mid-mountain, and summit coordinates; custom locations are explicitly marked as estimated geometry until curated points are supplied.
- **🌬️ Mass-conserving terrain guidance**: Publishes sheltered, neutral, and exposed SRU ranges without claiming an exact 10 m snow map.
- **🤖 Ensemble processing**: Evaluates available ensemble members independently and reports snowfall spread rather than deriving snow from averaged atmospheric fields.
- **📱 PWA & Offline Support**: Installed as a native-feeling app with persistent caching and "stale-while-error" resilience for the deep backcountry.
- **🌍 Dynamic Timezones & Coordinates**: Location-aware processing ensures you see the data exactly as it happens on the mountain.

## 📡 Supported Weather Models

MySnow orchestrates data from the world's leading meteorological agencies:

| Model | Resolution | Range | Best For |
| :--- | :--- | :--- | :--- |
| **NCEP HRRR** | 3 km | 0–48H | US Mountain precision & rapid refresh. |
| **GEM HRDPS** | 2.5 km | 0–48H | Canadian Rockies & deep Western valleys. |
| **ECMWF IFS** | 9 km | 3–10D | The global gold standard for mid-range. |
| **ECMWF AIFS** | 0.25° | 0–15D | Next-generation AI-driven efficiency. |
| **NCEP NBM** | 2.5 km | 0–7D | Calibrated consensus blend for US. |
| **Blended** | Variable | 0–15D | Optimal HRRR (Short) + ECMWF (Long) fusion. |

## 🧪 The Science of Snow (SLR Algorithms)

MySnow calculates frozen precipitation separately from fresh-snow density. The production choices are:

- **Kuchera**: deterministic temperature-profile baseline and current default.
- **Cobb 2011**: cloud, ascent, layer thickness and RH-over-ice weighting; it explicitly falls back to Kuchera when its required profile data are unavailable.
- **Fixed 10:1**: comparison baseline.
- **Open-Meteo snowfall**: model-output comparison.

Surface modifiers are versioned but disabled until backtesting calibrates them. The UI shows the phase, frozen SWE, method, fallbacks and warnings for every precipitation hour.

Open-Meteo snowfall remains a fixed-conversion comparison product. MySnow never divides it by precipitation and presents the result as a model-native or observed SLR.

## Forecast product definitions

- **Reference fresh snowfall** is the fresh depth expected at a level, sheltered reference site before settling.
- **Natural slope accumulation** applies elevation-aware baseline forcing and mass-conserving directional deposition at Ski-Slope Reference Unit (SRU) scale.
- **Opening-time snow** is new natural snow remaining after explicit settling, melt, wind compaction, and modeled wind loss.
- **Total natural snowpack** combines old and new modeled layers.
- **Managed-piste depth** is withheld unless snowmaking, grooming, or transfer telemetry is available. Without it, the UI says that grooming and snowmaking are not included.

The initial terrain coefficients and deterministic uncertainty envelopes are marked `uncalibrated_mvp`. They are useful as transparent guidance and test fixtures, not as locally validated drift-depth predictions.

## 🛠️ Technical Stack

- **Framework**: [Next.js 16](https://nextjs.org/) (App Router)
- **Language**: [TypeScript](https://www.typescriptlang.org/)
- **Styling**: [Tailwind CSS 4](https://tailwindcss.com/)
- **Animations**: [Framer Motion](https://www.framer.com/motion/)
- **Icons**: [Lucide React](https://lucide.dev/)
- **Backend Orchestration**: [Open-Meteo API](https://open-meteo.com/)
- **PWA**: [@ducanh2912/next-pwa](https://www.npmjs.com/package/@ducanh2912/next-pwa)

## 🚀 Getting Started

1. **Clone the repository**:
   ```bash
   git clone https://github.com/yourusername/mysnow.git
   cd mysnow
   ```

2. **Install dependencies**:
   ```bash
   npm install
   ```

3. **Run the development server**:
   ```bash
   npm run dev
   ```

4. **Navigate to `http://localhost:3000`** and start tracking the powder.

## 🗄️ Data Management

MySnow includes a massive dataset of over 2MB of global ski areas (`src/data/ski-areas.json`). 

- **Adding/Updating Areas**: You can update the source location in `legacy/ski_areas.csv` and run the conversion script:
  ```bash
  node scripts/convert-ski-areas.mjs
  ```
- **Custom Locations**: Users can also add their own custom locations directly in the UI, which are persisted locally via browser storage.

## 📜 License & Credits

- **License**: Licensed under the [GNU Affero General Public License v3.0](LICENSE).
- **Weather Data**: Provided by [Open-Meteo](https://open-meteo.com/) (CC BY 4.0).
- **Special Thanks**: Data sourced from NOAA/NWS, ECMWF, ECCC, and the meteorological community.

---
*Built with ❤️ for the mountains.*
