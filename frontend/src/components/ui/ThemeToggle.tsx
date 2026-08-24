"use client";

import React, { useState, useEffect, useCallback } from "react";
import { Sun, Moon } from "lucide-react";

export type Theme = "dark" | "light";

function getInitialTheme(): Theme {
  if (typeof window === "undefined") return "dark";
  const stored = localStorage.getItem("hospital-theme") as Theme | null;
  if (stored === "dark" || stored === "light") return stored;
  return "dark";
}

function applyThemeToDOM(theme: Theme) {
  const root = document.documentElement;
  if (theme === "dark") {
    root.classList.add("dark");
    root.classList.remove("light");
  } else {
    root.classList.add("light");
    root.classList.remove("dark");
  }
  localStorage.setItem("hospital-theme", theme);
}

interface ThemeToggleProps {
  theme: Theme;
  onThemeChange: (theme: Theme) => void;
}

export default function ThemeToggle({ theme, onThemeChange }: ThemeToggleProps) {
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    const initial = getInitialTheme();
    applyThemeToDOM(initial);
    onThemeChange(initial);
    setMounted(true);
  }, []);

  const toggle = useCallback(() => {
    const next = theme === "dark" ? "light" : "dark";
    applyThemeToDOM(next);
    onThemeChange(next);
  }, [theme, onThemeChange]);

  if (!mounted) {
    return (
      <div className="theme-toggle" style={{ opacity: 0 }}>
        <Moon size={14} />
        <span>Theme</span>
      </div>
    );
  }

  return (
    <button onClick={toggle} className="theme-toggle" title={`Switch to ${theme === "dark" ? "light" : "dark"} mode`}>
      {theme === "dark" ? <Sun size={14} /> : <Moon size={14} />}
      <span>{theme === "dark" ? "Light" : "Dark"}</span>
    </button>
  );
}
