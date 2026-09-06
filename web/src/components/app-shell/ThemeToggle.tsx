import { Monitor, Moon, Sun } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useTheme, type ThemePreference } from "@/lib/theme";

const ICONS: Record<ThemePreference, typeof Sun> = { light: Sun, dark: Moon, system: Monitor };
const LABELS: Record<ThemePreference, string> = {
  light: "Light theme",
  dark: "Dark theme",
  system: "System theme",
};

/** Cycles light -> dark -> system; stored as `copycast.theme`. */
export function ThemeToggle() {
  const { preference, cycle } = useTheme();
  const Icon = ICONS[preference];
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button
          variant="ghost"
          size="icon"
          onClick={cycle}
          aria-label={`${LABELS[preference]}; switch theme`}
        >
          <Icon />
        </Button>
      </TooltipTrigger>
      <TooltipContent>{LABELS[preference]}</TooltipContent>
    </Tooltip>
  );
}
