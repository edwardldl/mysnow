"use client";

import React from "react";
import { cn } from "@/lib/utils_tailwind";
import { DayData } from "@/lib/types";
import { Snowflake } from "lucide-react";
import { getSlrColor, getWeatherDescription } from "@/lib/utils";
import { motion } from "framer-motion";

interface DateRibbonProps {
  days: DayData[];
  selectedDate: string | null;
  onSelect: (date: string) => void;
}

export default function DateRibbon({ days, selectedDate, onSelect }: DateRibbonProps) {
  return (
    <div className="w-full py-3 px-4 md:px-8 overflow-x-auto no-scrollbar">
      <div className="max-w-7xl mx-auto flex gap-3 md:gap-4">
        {days.map((day) => {
          const isSelected = day.dateStr === selectedDate;
          const todayStr = new Date().toLocaleDateString('en-CA', { timeZone: 'America/Los_Angeles' });
          const isToday = day.dateStr === todayStr;
          const date = new Date(day.dateStr + 'T12:00:00'); // Use midday to avoid TZ shifts
          const dayName = isToday ? "Today" : new Intl.DateTimeFormat('en-US', { weekday: 'short' }).format(date);
          const dayNum = day.dateStr.split('-')[2];

          // NEW: Storm & SLR Logic
          const isStorm = day.totalSnowfall >= 15;
          const avgSlr = day.totalPrecipitation > 0 ? (day.totalSnowfall * 10) / day.totalPrecipitation : 0;
          const slrColor = getSlrColor(avgSlr);

          return (
            <button
              key={day.dateStr}
              onClick={() => onSelect(day.dateStr)}
              className={cn(
                "flex flex-col items-center min-w-[60px] md:min-w-[70px] py-2 rounded-xl transition-all duration-300 relative group overflow-hidden",
                isSelected 
                  ? "bg-accent-blue text-white shadow-lg shadow-blue-500/20" 
                  : (isStorm ? "text-white" : "bg-white/5 text-slate-400 hover:bg-white/10 hover:text-slate-200")
              )}
            >
              {/* Storm Pulsing Background */}
              {isStorm && !isSelected && (
                <motion.div
                  animate={{ 
                    opacity: [0.6, 1, 0.6],
                    scale: [0.95, 1.05, 0.95],
                  }}
                  transition={{ repeat: Infinity, duration: 3, ease: "easeInOut" }}
                  className="absolute inset-0 z-0"
                  style={{ backgroundColor: slrColor }}
                />
              )}

              <div className="relative z-10 flex flex-col items-center w-full px-1">
                <div className="flex items-center gap-1 mb-0.5">
                  <span className={cn(
                    "text-[9px] font-black uppercase tracking-tighter opacity-80",
                    isStorm && !isSelected && "text-white/90"
                  )}>
                    {dayName}
                  </span>
                  <span className="text-xs">{getWeatherDescription(day.weatherCode).icon}</span>
                </div>
                
                <span className={cn(
                  "text-lg font-black leading-none mb-1",
                  isStorm && !isSelected && "text-white"
                )}>
                  {dayNum}
                </span>

                <div className="flex flex-col items-center gap-0.5 mt-auto">
                  <div className="flex items-center gap-1.5 text-[9px] font-black tabular-nums">
                    <span className="text-rose-400">{day.maxTemp.toFixed(0)}°</span>
                    <span className="text-blue-400">{day.minTemp.toFixed(0)}°</span>
                  </div>
                  
                  {day.totalSnowfall > 0 && (
                    <div className="flex items-center gap-0.5 bg-accent-blue/20 px-1 rounded-sm">
                      <Snowflake className={cn(
                        "w-2 h-2", 
                        isSelected ? "text-white" : (isStorm ? "text-white" : "text-accent-cyan")
                      )} />
                      <span className={cn(
                        "text-[9px] font-black",
                        isStorm && !isSelected ? "text-white" : "text-accent-cyan"
                      )}>{day.totalSnowfall.toFixed(0)}</span>
                    </div>
                  )}
                </div>
              </div>

              {isSelected && (
                <div className="absolute -bottom-1 left-1/2 -translate-x-1/2 w-1 h-1 bg-white rounded-full z-10" />
              )}
            </button>
          );
        })}
      </div>
    </div>
  );
}
