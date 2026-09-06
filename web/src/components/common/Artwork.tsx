import { Disc3 } from "lucide-react";
import { useState } from "react";

import { cn } from "@/lib/utils";

/** Feed or Episode Artwork with a neutral fallback; never composes URLs. */
export function Artwork({
  src,
  alt = "",
  size = 40,
  className,
}: {
  src: string | null | undefined;
  alt?: string;
  size?: number;
  className?: string;
}) {
  const [failed, setFailed] = useState(false);
  const style = { width: size, height: size };
  if (!src || failed) {
    return (
      <div
        className={cn(
          "flex shrink-0 items-center justify-center rounded-md bg-muted text-muted-foreground",
          className,
        )}
        style={style}
        role={alt ? "img" : undefined}
        aria-label={alt || undefined}
        aria-hidden={alt ? undefined : true}
      >
        <Disc3 style={{ width: size / 2, height: size / 2 }} />
      </div>
    );
  }
  return (
    <img
      src={src}
      alt={alt}
      width={size}
      height={size}
      loading="lazy"
      decoding="async"
      onError={() => setFailed(true)}
      className={cn("shrink-0 rounded-md bg-muted object-cover", className)}
      style={style}
    />
  );
}
