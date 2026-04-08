# MySnow ❄️

**MySnow** is a premium, high-resolution ski forecast web application designed specifically for California resorts. It provides a data-dense, intuitive interface for skiers and snowboarders to track upcoming storms with professional-grade accuracy.

## 🚀 Quick Start

To run the app automatically (Mac only):

1. **Open your Terminal.**
2. **Navigate to the project folder:**
   ```bash
   cd /Users/edw4rdldl/Documents/calisnow
   ```
3. **Run the start script:**
   ```bash
   ./start.sh
   ```

Or start it manually:
```bash
python3 -m http.server 8080
```
Then open [http://localhost:8080](http://localhost:8080) in your browser.

## ✨ Key Features

- **Dual-Model Blending**: Combines high-res **HRRR** (0-48h) for short-term precision with **ECMWF IFS** for long-range planning.
- **Advanced Visualizations**:
  - **Snow Activity Bars**: Color-coded by Snow-to-Liquid Ratio (SLR) using a continuous gradient (Green for Rain → Blue for Slush → Orange for Powder).
  - **Temperature Trends**: Dynamic SVG line charts showing hourly temperature fluctuations.
  - **9am - 4pm Markers**: Vertical laser lines highlighting the core ski day.
- **Custom Locations**: Add your favorite secret stashes or specific coordinates. Data is persisted locally in your browser.
- **Telemetry Grid**: Comprehensive breakdown of wind speeds (km/h), gusts, feels-like temp, snow level (meters), humidity, and cloud cover.
- **Astro Tracking**: Integrated sunrise (🌅) and sunset (🌇) indicators for dawn patrol and après planning.

## 📊 How to Read the Charts

- **Snow Bars**: The length represents snowfall amount (maxed at 3cm/hr for consistent scaling).
- **SLR Gradient**: 
  - **Green**: Rain (0-1:1)
  - **Blue**: Wet/Heavy Snow (~5:1)
  - **Orange**: Ultra-Light Powder (>20:1)
- **Lines**: The glowing lines show the trend of **Temperature** and across the 24-hour cycle.
- **Blue Laser Lines**: These mark the standard lift operating window (09:00 - 16:00).

## 🛠 Technology Stack

- **Frontend**: Vanilla HTML5, CSS3, JavaScript (ES6 Modules).
- **API**: [Open-Meteo](https://open-meteo.com/) for high-resolution weather model data.
- **Styling**: Pure CSS with Glassmorphism aesthetics and responsive design.

---
*Stay stoked and track the deep days!* 🎿🏔️
