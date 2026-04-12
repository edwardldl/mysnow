"use client";

import React from "react";
import { RefreshCw, Snowflake, Thermometer, Wind, Sun, Navigation2 } from "lucide-react";
import { cn } from "@/lib/utils_tailwind";
import { BlendedHour } from "@/lib/types";
import { getWeatherDescription } from "@/lib/utils";

interface HeaderProps {
  mode: "forecast" | "history";
  setMode: (mode: "forecast" | "history") => void;
  onRefresh: () => void;
  isLoading?: boolean;
  currentData?: BlendedHour | null;
}

export default function Header({ mode, setMode, onRefresh, isLoading, currentData }: HeaderProps) {
  const weather = currentData ? getWeatherDescription(currentData.weatherCode) : null;

  return (
    <header className="fixed top-0 left-0 right-0 z-50 w-full h-[calc(72px+var(--sat,0px))] md:h-[calc(88px+var(--sat,0px))] pt-[var(--sat,0px)] glass-panel border-b border-white/5 shadow-2xl overflow-hidden">
      <div className="max-w-7xl mx-auto px-4 md:px-8 h-full flex items-center justify-between gap-4">
        {/* Title & Branding */}
        <div className="flex items-center gap-2 md:gap-3 shrink-0">
          <div className="p-1.5 md:p-2 bg-accent-blue rounded-lg md:rounded-xl neon-glow-cyan">
            <Snowflake className="w-4 h-4 md:w-6 md:h-6 text-white animate-pulse-soft" />
          </div>
          <div className="hidden sm:block">
            <h1 className="text-lg md:text-xl font-bold tracking-tight text-white m-0 leading-tight">
              MySnow
            </h1>
            <p className="text-[8px] md:text-[9px] uppercase tracking-widest text-slate-500 font-bold opacity-80">
              Advanced Ski Forecasting
            </p>
          </div>
          
          <button 
            onClick={onRefresh}
            disabled={isLoading}
            className={cn(
              "ml-1 md:ml-4 p-1.5 md:p-2 rounded-full bg-white/5 border border-white/10 text-slate-300 hover:text-white hover:bg-white/10 transition-all",
              isLoading && "animate-spin text-accent-cyan"
            )}
          >
            <RefreshCw className="w-4 h-4 md:w-5 md:h-5" />
          </button>
        </div>

        {/* Current Metrics - Scrollable on mobile */}
        <div className="flex items-center gap-4 md:gap-8 overflow-x-auto no-scrollbar py-2 max-w-[40%] sm:max-w-[50%] md:max-w-none">
           {weather && (
             <div className="flex flex-col items-center shrink-0 min-w-[40px]">
               <span className="text-lg md:text-xl leading-none">{weather.icon}</span>
               <span className="text-[9px] font-black text-slate-500 uppercase">{weather.label}</span>
             </div>
           )}
           
           <HeaderMetric 
             icon={<Thermometer className="w-3 md:w-3.5 h-3 md:h-3.5 text-rose-400" />}
             label="TEMP"
             value={currentData?.temperature != null ? `${currentData.temperature.toFixed(1)}°` : "--"}
           />

           <HeaderMetric 
             icon={<Thermometer className="w-3 md:w-3.5 h-3 md:h-3.5 text-rose-300 opacity-60" />}
             label="FEELS"
             value={currentData?.feelsLike != null ? `${currentData.feelsLike.toFixed(1)}°` : "--"}
           />

           <HeaderMetric 
             icon={<Snowflake className="w-3 md:w-3.5 h-3 md:h-3.5 text-accent-cyan" />}
             label="RATE"
             value={currentData?.snowfall != null ? `${currentData.snowfall.toFixed(1)}` : "0.0"}
             unit="cm/h"
           />

           <HeaderMetric 
             icon={<Wind className="w-3 md:w-3.5 h-3 md:h-3.5 text-emerald-400" />}
             label="WIND"
             value={currentData?.windSpeed != null ? `${(currentData.windSpeed * 3.6).toFixed(0)}` : "--"}
             unit="km/h"
             extra={
               <div className="flex items-center gap-1 ml-1.5">
                 {currentData?.windDir != null && (
                   <Navigation2 
                     className="w-2.5 h-2.5 text-emerald-300" 
                     style={{ transform: `rotate(${(currentData.windDir || 0) + 180}deg)`, fill: 'currentColor' }} 
                   />
                 )}
                 {currentData?.gusts != null && currentData.gusts > (currentData.windSpeed || 0) && (
                   <span className="text-[9px] font-black text-emerald-400 opacity-60">
                     G:{(currentData.gusts * 3.6).toFixed(0)}
                   </span>
                 )}
               </div>
             }
           />

           <HeaderMetric 
              icon={<Sun className="w-3 md:w-3.5 h-3 md:h-3.5 text-yellow-400" />}
              label="UV"
              value={currentData?.uvIndex != null ? currentData.uvIndex.toFixed(1) : "--"}
           />
        </div>

        {/* Mode Switcher */}
        <div className="flex bg-slate-900 pr-1 rounded-full border border-white/5 scale-90 md:scale-100 shrink-0">
          <button
            onClick={() => setMode("forecast")}
            className={cn(
              "px-3 md:px-5 py-1.5 rounded-full text-[10px] md:text-xs font-black transition-all",
              mode === "forecast" ? "bg-accent-blue text-white" : "text-slate-500 hover:text-slate-300"
            )}
          >
            FORECAST
          </button>
          <button
            onClick={() => setMode("history")}
            className={cn(
              "px-3 md:px-5 py-1.5 rounded-full text-[10px] md:text-xs font-black transition-all",
              mode === "history" ? "bg-accent-rose text-white" : "text-slate-500 hover:text-slate-300"
            )}
          >
            HISTORY
          </button>
        </div>
      </div>
    </header>
  );
}

function HeaderMetric({ icon, label, value, unit, extra }: { icon: React.ReactNode, label: string, value: string, unit?: string, extra?: React.ReactNode }) {
  return (
    <div className="flex flex-col shrink-0">
      <div className="flex items-center gap-1.5 mb-0.5">
        {icon}
        <span className="text-[10px] uppercase font-black text-slate-500 tracking-tighter">{label}</span>
      </div>
      <div className="flex items-center gap-0.5">
        <span className="text-xs font-bold text-white tracking-tight">{value}</span>
        {unit && <span className="text-[9px] font-black text-slate-500 ml-0.5">{unit}</span>}
        {extra}
      </div>
    </div>
  );
}
