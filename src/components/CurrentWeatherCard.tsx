"use client";

import React from "react";
import { 
  Thermometer, 
  Wind, 
  Snowflake, 
  Sun, 
  Eye, 
  Navigation2,
  Clock
} from "lucide-react";
import { cn } from "@/lib/utils_tailwind";
import { BlendedHour } from "@/lib/types";
import { getWeatherDescription } from "@/lib/utils";
import { motion } from "framer-motion";

interface CurrentWeatherCardProps {
  currentData: BlendedHour | null;
  locationName: string;
  className?: string;
}

export default function CurrentWeatherCard({ currentData, locationName, className }: CurrentWeatherCardProps) {
  const weather = currentData ? getWeatherDescription(currentData.weatherCode) : null;
  
  if (!currentData) return null;

  return (
    <motion.div 
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.6, ease: [0.16, 1, 0.3, 1] }}
      className={cn("w-full max-w-7xl mx-auto px-4 md:px-8 py-6", className)}
    >
      <div className="glass-panel rounded-[2rem] overflow-hidden border-white/10 shadow-3xl">
        <div className="relative p-6 md:p-10 flex flex-col lg:flex-row lg:items-center justify-between gap-8">
          
          {/* Main Info */}
          <div className="flex flex-col md:flex-row md:items-center gap-6 md:gap-10">
            <div className="flex items-center justify-center w-24 h-24 md:w-32 md:h-32 rounded-3xl bg-white/5 border border-white/10 shadow-inner">
              <span className="text-5xl md:text-7xl drop-shadow-lg">
                {weather?.icon || "—"}
              </span>
            </div>
            
            <div className="flex flex-col">
              <div className="flex items-center gap-2 mb-1">
                <span className="px-2 py-0.5 rounded-full bg-accent-blue/20 text-accent-blue text-[10px] font-black uppercase tracking-widest border border-accent-blue/20">Now</span>
                <span className="text-slate-400 text-xs font-bold flex items-center gap-1">
                   <Clock className="w-3 h-3" /> {new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                </span>
              </div>
              <h2 className="text-2xl md:text-4xl font-bold text-white tracking-tight mb-1">
                {locationName}
              </h2>
              <div className="flex items-center gap-4">
                <span className="text-5xl md:text-7xl font-black text-white tracking-tighter">
                  {currentData.temperature.toFixed(1)}°
                </span>
                <div className="flex flex-col">
                  <span className="text-lg md:text-xl font-medium text-slate-300">
                    {weather?.label}
                  </span>
                  <div className="flex items-center gap-1 text-slate-500 font-bold text-xs uppercase tracking-tight">
                    <Thermometer className="w-3 h-3 text-rose-400" />
                    Feels like {currentData.feelsLike?.toFixed(1)}°
                  </div>
                </div>
              </div>
            </div>
          </div>

          {/* Stats Grid */}
          <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-2 xl:grid-cols-4 gap-4 w-full lg:w-auto">
            <StatBox 
              icon={<Wind className="w-4 h-4 text-emerald-400" />}
              label="Wind"
              value={`${(currentData.windSpeed! * 3.6).toFixed(0)}`}
              unit="km/h"
              extra={
                <div className="flex items-center gap-1.5 mt-1">
                  <Navigation2 
                    className="w-3 h-3 text-emerald-300"
                    style={{ transform: `rotate(${(currentData.windDir || 0) + 180}deg)`, fill: 'currentColor' }}
                  />
                  {currentData.gusts != null && currentData.gusts > (currentData.windSpeed || 0) && (
                    <span className="text-[10px] font-black text-emerald-400/60 uppercase">
                      G:{(currentData.gusts * 3.6).toFixed(0)}
                    </span>
                  )}
                </div>
              }
            />
            <StatBox 
              icon={<Snowflake className="w-4 h-4 text-accent-cyan" />}
              label="Snow Rate"
              value={currentData.snowfall.toFixed(1)}
              unit="cm/h"
            />
            <StatBox 
              icon={<Sun className="w-4 h-4 text-yellow-400" />}
              label="UV Index"
              value={currentData.uvIndex?.toFixed(1) || "0.0"}
              unit={currentData.uvIndex != null ? (currentData.uvIndex > 5 ? "High" : "Low") : ""}
            />
            <StatBox 
              icon={<Eye className="w-4 h-4 text-violet-400" />}
              label="Visibility"
              value={currentData.visibility != null ? (currentData.visibility / 1000).toFixed(1) : "--"}
              unit="km"
            />
          </div>

        </div>
        
        {/* Animated Background Accent */}
        <div className="absolute top-0 right-0 w-64 h-64 bg-accent-blue/10 rounded-full blur-[100px] pointer-events-none -z-10 translate-x-1/2 -translate-y-1/2" />
        <div className="absolute bottom-0 left-0 w-64 h-64 bg-accent-cyan/5 rounded-full blur-[100px] pointer-events-none -z-10 -translate-x-1/2 translate-y-1/2" />
      </div>
    </motion.div>
  );
}

function StatBox({ icon, label, value, unit, extra }: { 
  icon: React.ReactNode, 
  label: string, 
  value: string, 
  unit?: string,
  extra?: React.ReactNode
}) {
  return (
    <div className="glass-card p-4 rounded-2xl flex flex-col">
      <div className="flex items-center gap-2 mb-2">
        {icon}
        <span className="text-[10px] font-black text-slate-500 uppercase tracking-widest">{label}</span>
      </div>
      <div className="flex items-baseline gap-1">
        <span className="text-xl font-bold text-white tracking-tight">{value}</span>
        {unit && <span className="text-[10px] font-bold text-slate-500">{unit}</span>}
      </div>
      {extra}
    </div>
  );
}
