# MySnow ❄️

**MySnow** is a premium, high-resolution ski forecast engine. It provides a data-dense, physics-backed interface for trackers who need professional-grade accuracy in their snowfall predictions.

## 🚀 Getting Started

### Prerequisites
- Node.js (Latest LTS)
- npm or yarn

### Installation
1.  **Clone the repository.**
2.  **Install dependencies**:
    ```bash
    npm install
    ```
3.  **Start Dev Server**:
    ```bash
    npm run dev
    ```
4.  **Production Build**:
    ```bash
    npm run build
    ```

## ✨ Core Technology

- **TypeScript Engine**: Fully type-safe data pipeline for robust meteorological calculations.
- **Physics-Based Snow Level**: Dynamic estimations accounting for evaporative and diabatic cooling during heavy storms.
- **Multi-Algorithm SLR**: Choose between Sierra Custom, Kuchera, Roebber, and Cobb models for ground-truth snow quality.
- **Dual-Model Blending**: Fuses high-res **HRRR** (3km) with global **ECMWF** (9km).

## 📊 Key Interface Features

- **Segmented Toggles**: Modern UI for switching between "Best Match" models and "Snow Physics" algorithms.
- **Triple-Layer Synchronized Charts**:
  - **Snow Intensity**: Bar chart for accumulation.
  - **Temperature SVG**: Fluid line graph with dynamic scaling.
  - **Telemetry Grid**: Humidity, Precip %, Snow Level, Wind Gusts, and Cloud Cover.
- **Premium Aesthetics**: Glassmorphism design with responsive support for tablets and mobile.
- **Local Stash Management**: Add and persist your own secret ski spots via coordinates.

## 🛠 Tech Stack
- **Build Tool**: Vite
- **Logic**: TypeScript ES6
- **Styling**: Vanilla CSS3
- **Data**: [Open-Meteo](https://open-meteo.com/)

---
*Stay stoked and track the deep days!* 🎿🏔️
