"use client";

import React from "react";
import { cn } from "@/lib/utils_tailwind";
import { DayData } from "@/lib/types";
import { Snowflake } from "lucide-react";

interface DateRibbonProps {
  days: DayData[];
  selectedDate: string | null;
  onSelect: (date: string) => void;
}

export default function DateRibbon({ days, selectedDate, onSelect }: DateRibbonProps) {
  return (
    <div className="w-full bg-slate-950/80 backdrop-blur-xl border-b border-white/5 py-3 px-4 md:px-8 overflow-x-auto no-scrollbar shadow-xl">
      <div className="max-w-7xl mx-auto flex gap-3 md:gap-4">
        {days.map((day) => {
          const isSelected = day.dateStr === selectedDate;
          const todayStr = new Date().toLocaleDateString('en-CA', { timeZone: 'America/Los_Angeles' });
          const isToday = day.dateStr === todayStr;
          const date = new Date(day.dateStr + 'T12:00:00'); // Use midday to avoid TZ shifts
          const dayName = isToday ? "Today" : new Intl.DateTimeFormat('en-US', { weekday: 'short' }).format(date);
          const dayNum = day.dateStr.split('-')[2];

          return (
            <button
              key={day.dateStr}
              onClick={() => onSelect(day.dateStr)}
              className={cn(
                "flex flex-col items-center min-w-[60px] md:min-w-[70px] py-2 rounded-xl transition-all duration-300 relative group",
                isSelected 
                  ? "bg-accent-blue text-white shadow-lg shadow-blue-500/20" 
                  : "bg-white/5 text-slate-400 hover:bg-white/10 hover:text-slate-200"
              )}
            >
              <span className="text-[10px] font-black uppercase tracking-tighter opacity-80 mb-0.5">
                {dayName}
              </span>
              <span className="text-sm font-black">
                {dayNum}
              </span>
              
              {day.totalSnowfall > 0 && (
                <div className="mt-1 flex items-center gap-0.5">
                  <Snowflake className={cn("w-2 h-2", isSelected ? "text-white" : "text-accent-cyan")} />
                  <span className="text-[9px] font-black">{day.totalSnowfall.toFixed(0)}</span>
                </div>
              )}

              {isSelected && (
                <div className="absolute -bottom-1 left-1/2 -translate-x-1/2 w-1 h-1 bg-white rounded-full" />
              )}
            </button>
          );
        })}
      </div>
    </div>
  );
}
