# 🏔️ MySnow: High-Fidelity Snow Forecasting

MySnow is a premium, state-of-the-art weather forecasting application specifically engineered for skiers, snowboarders, and mountain enthusiasts. It combines high-resolution meteorological models with advanced snow physics to provide the most accurate snowfall estimates available.

![MySnow Preview](public/preview.png) *(Note: Add your own preview image here)*

## 🌟 Key Features

- **🎯 Multi-Resolution Modelling**: Seamlessly switch between regional high-resolution models (3km) and global premier models (9km).
- **❄️ Advanced Snow Physics**: Choose from multiple Snow-to-Liquid Ratio (SLR) algorithms including Hybrid Cobb-Kuchera and Dendritic Habit diagrams.
- **⛰️ Elevation-Aware Simulation**: View precise forecasts for the Base, Mid-mountain, or Peak of any resort with automated statistical downscaling.
- **🤖 Next-Gen AI Integration**: Leverages ECMWF AIFS (AI-driven forecasting) and probabilistic ensemble averages for long-range precision.
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

Standard apps assume a 10:1 snow-to-water ratio. MySnow knows better. We use physics-based logic to fragment and compact snow based on temperature aloft, wind shear, and moisture:

- **Hybrid (Kuchera-Cobb)**: Physical depth + wind compaction boost.
- **Cobb (DGZ-Enhanced)**: Focuses on saturated Dendritic Growth Zone lift.
- **Dendro**: Uses crystal habit diagrams and DGZ depth for high-precision fluff.
- **KRC-Comp**: Advanced riming and fragmentation logic for high-fidelity density.

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

