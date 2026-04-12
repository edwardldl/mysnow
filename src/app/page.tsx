"use client";

import React, { useState, useEffect, useCallback } from "react";
import Header from "@/components/Header";
import DateRibbon from "@/components/DateRibbon";
import LocationSelector from "@/components/LocationSelector";
import ControlSection from "@/components/ControlSection";
import { cn } from "@/lib/utils_tailwind";
import ForecastDashboard from "@/components/ForecastDashboard";
import HistorySection from "@/components/HistorySection";
import CreditsFooter from "@/components/CreditsFooter";
import { fetchWeatherData, getLocations, saveLocation, removeLocation } from "@/lib/api";
import { blendForecasts, groupData } from "@/lib/data";
import { Location, DayData } from "@/lib/types";
import { motion, AnimatePresence } from "framer-motion";

export default function Home() {
  const [mode, setMode] = useState<"forecast" | "history">("forecast");
  const [locations, setLocations] = useState<Record<string, Location>>({});
  const [currentLocationId, setCurrentLocationId] = useState("palisades");
  const [modelId, setModelId] = useState("best_match");
  const [algoId, setAlgoId] = useState("hybrid");
  
  const [forecastDays, setForecastDays] = useState<DayData[]>([]);
  const [selectedDate, setSelectedDate] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Initialize locations
  useEffect(() => {
    setLocations(getLocations());
  }, []);

  const loadData = useCallback(async (locId: string, model: string, algo: string) => {
    setIsLoading(true);
    setError(null);
    try {
      const data = await fetchWeatherData(locId, model);
      const blended = blendForecasts(data.hrrrData, data.ecmwfData, data.location, algo, data.mode);
      const grouped = groupData(blended);
      setForecastDays(grouped);
      
      // Ensure the initial selection is the first day of the new dataset
      if (grouped.length > 0) {
        setSelectedDate(grouped[0].dateStr);
      }
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to fetch weather data");
      console.error(err);
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    if (Object.keys(locations).length > 0) {
      loadData(currentLocationId, modelId, algoId);
    }
  }, [currentLocationId, modelId, algoId, locations, loadData]);

  const handleAddLocation = (name: string, lat: string, lon: string) => {
    const id = name.toLowerCase().replace(/\s+/g, '-');
    const newLocs = saveLocation(id, name, lat, lon);
    setLocations(newLocs);
    setCurrentLocationId(id);
  };

  const handleRemoveLocation = (id: string) => {
    const newLocs = removeLocation(id);
    setLocations(newLocs);
    if (currentLocationId === id) {
      setCurrentLocationId("palisades");
    }
  };

  return (
    <div className="min-h-screen bg-background flex flex-col font-sans">
      <Header 
        mode={mode} 
        setMode={setMode} 
        onRefresh={() => loadData(currentLocationId, modelId, algoId)}
        isLoading={isLoading}
        currentData={forecastDays.length > 0 && forecastDays[0].hourly.length > 0 ? forecastDays[0].hourly[0] : null}
      />

      {mode === "forecast" && (
        <div className="fixed top-[calc(72px+var(--sat,0px))] md:top-[calc(88px+var(--sat,0px))] left-0 right-0 z-40">
          <DateRibbon 
            days={forecastDays} 
            selectedDate={selectedDate} 
            onSelect={setSelectedDate} 
          />
        </div>
      )}

      <main className={cn(
        "flex-1 overflow-y-auto no-scrollbar scroll-smooth text-slate-50",
        mode === "forecast" ? "pt-[calc(136px+var(--sat,0px))] md:pt-[calc(160px+var(--sat,0px))]" : "pt-[calc(72px+var(--sat,0px))] md:pt-[calc(88px+var(--sat,0px))]"
      )}>
        <AnimatePresence mode="wait">
          {mode === "forecast" ? (
            <motion.div 
              key="forecast"
              initial={{ opacity: 0, scale: 0.98 }}
              animate={{ opacity: 1, scale: 1 }}
              exit={{ opacity: 0, scale: 1.02 }}
              transition={{ duration: 0.5, ease: "easeOut" }}
            >
              <LocationSelector 
                locations={locations}
                currentLocationId={currentLocationId}
                onSelect={setCurrentLocationId}
                onAdd={handleAddLocation}
                onRemove={handleRemoveLocation}
              />
              
              <ControlSection 
                modelId={modelId}
                setModelId={setModelId}
                algoId={algoId}
                setAlgoId={setAlgoId}
              />

              {error ? (
                <div className="max-w-7xl mx-auto px-4 md:px-8 mt-12 pb-20">
                  <div className="glass-panel p-8 rounded-3xl border-accent-rose/20 text-center">
                    <h3 className="text-xl font-bold text-accent-rose mb-2">Error Loading Data</h3>
                    <p className="text-slate-400">{error}</p>
                    <button 
                      onClick={() => loadData(currentLocationId, modelId, algoId)}
                      className="mt-6 px-6 py-2 bg-accent-rose text-white rounded-full font-bold text-sm shadow-lg shadow-rose-500/20"
                    >
                      Retry
                    </button>
                  </div>
                </div>
              ) : (
                <ForecastDashboard 
                  days={forecastDays} 
                  isLoading={isLoading}
                  selectedDate={selectedDate}
                />
              )}
            </motion.div>
          ) : (
            <motion.div 
              key="history"
              initial={{ opacity: 0, x: 20 }}
              animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0, x: -20 }}
              transition={{ duration: 0.4 }}
              className="pb-20"
            >
              <HistorySection currentLocationId={currentLocationId} />
            </motion.div>
          )}
        </AnimatePresence>
        
        <CreditsFooter />
      </main>

      {/* Decorative Background Elements */}
      <div className="fixed top-0 left-0 w-full h-full -z-50 overflow-hidden pointer-events-none">
        <div className="absolute top-[-10%] right-[-10%] w-[40%] h-[40%] bg-accent-blue/5 rounded-full blur-[120px] animate-pulse-soft" />
        <div className="absolute bottom-[-10%] left-[-10%] w-[40%] h-[40%] bg-accent-violet/5 rounded-full blur-[120px] animate-pulse-soft " style={{ animationDelay: '1.5s' }} />
      </div>
    </div>
  );
}
