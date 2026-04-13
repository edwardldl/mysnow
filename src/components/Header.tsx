"use client";

import React from "react";
import { RefreshCw, Snowflake } from "lucide-react";
import { cn } from "@/lib/utils_tailwind";
import { BlendedHour, Location } from "@/lib/types";
import { getWeatherDescription } from "@/lib/utils";
import LocationDropdown from "./LocationDropdown";

interface HeaderProps {
  onRefresh: () => void;
  isLoading?: boolean;
  currentData?: BlendedHour | null;
  locations: Record<string, Location>;
  currentLocationId: string;
  onSelectLocation: (id: string) => void;
  onAddLocation: (name: string, lat: string, lon: string) => void;
  onRemoveLocation: (id: string) => void;
}

export default function Header({
  onRefresh,
  isLoading,
  currentData,
  locations,
  currentLocationId,
  onSelectLocation,
  onAddLocation,
  onRemoveLocation,
}: HeaderProps) {
  const weather = currentData ? getWeatherDescription(currentData.weatherCode) : null;

  // Debug: log isLoading changes
  // console.log("Header isLoading:", isLoading);

  return (
    <header className="fixed top-0 left-0 right-0 z-50 w-full h-[calc(60px+var(--sat,0px))] md:h-[calc(72px+var(--sat,0px))] pt-[var(--sat,0px)] glass-panel border-b border-white/5 shadow-2xl transition-all duration-300">
      <div className="max-w-7xl mx-auto px-4 md:px-8 h-full flex items-center justify-between gap-4">
        {/* Left Column: Branding */}
        <div className="flex items-center gap-2 md:gap-4 shrink-0">
          <div className="flex items-center gap-2 md:gap-3">
            <div className="p-1.5 md:p-2 bg-accent-blue rounded-lg md:rounded-xl neon-glow-cyan text-white">
              <Snowflake className="w-4 h-4 md:w-5 md:h-5 animate-pulse-soft" />
            </div>
            <div className="hidden sm:block">
              <h1 className="text-base md:text-lg font-bold tracking-tight text-white m-0 leading-tight">
                MySnow
              </h1>
              <p className="text-[7px] md:text-[8px] uppercase tracking-widest text-slate-500 font-bold opacity-80">
                Advanced Ski Forecasting
              </p>
            </div>
          </div>
        </div>

        {/* Right Column: Actions */}
        <div className="flex items-center gap-2 md:gap-3 shrink-0 ml-auto">
          <LocationDropdown
            locations={locations}
            currentLocationId={currentLocationId}
            onSelect={onSelectLocation}
            onAdd={onAddLocation}
            onRemove={onRemoveLocation}
          />

          {currentData && (
            <div className="hidden md:flex items-center gap-3 px-3 py-1.5 rounded-full bg-white/5 border border-white/10">
              <span className="text-sm md:text-base">{weather?.icon}</span>
              <span className="text-xs md:text-sm font-bold text-white">
                {currentData.temperature.toFixed(1)}°
              </span>
            </div>
          )}

          <button
            onClick={onRefresh}
            disabled={isLoading}
            className={cn(
              "p-2 md:p-2.5 rounded-full bg-white/5 border border-white/10 text-slate-300 hover:text-white hover:bg-white/10 transition-all focus:outline-none focus:ring-2 focus:ring-accent-blue/50 active:scale-95 group shadow-sm disabled:opacity-50 disabled:cursor-not-allowed",
              isLoading && "text-accent-cyan ring-1 ring-accent-cyan/30 bg-accent-cyan/5"
            )}
            aria-label="Refresh data"
          >
            <div className={cn(
              "flex items-center justify-center transition-all duration-500",
              isLoading ? "animate-spin" : "group-hover:rotate-12 group-active:-rotate-45"
            )}>
              <RefreshCw className="w-4 h-4 md:w-5 md:h-5" />
            </div>
          </button>
        </div>
      </div>
    </header>


  );
}
