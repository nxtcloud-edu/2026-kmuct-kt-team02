import { AlertCircle, CheckCircle2, X } from "lucide-react";
import { cn } from "@/lib/cn";
import { useApp } from "@/providers/AppProvider";

export function ToastViewport() {
  const { toast, dismissToast } = useApp();

  return (
    <div className="pointer-events-none fixed bottom-6 left-1/2 z-[60] w-[min(24rem,calc(100vw-2rem))] -translate-x-1/2">
      {toast && (
        <div
          className={cn(
            "pointer-events-auto flex animate-fade-up items-start gap-3 rounded-2xl border bg-white px-4 py-3 shadow-panel",
            toast.tone === "error" ? "border-urgent-border" : "border-likely-border",
          )}
        >
          {toast.tone === "error" ? (
            <AlertCircle aria-hidden="true" className="mt-0.5 h-5 w-5 shrink-0 text-urgent" />
          ) : (
            <CheckCircle2 aria-hidden="true" className="mt-0.5 h-5 w-5 shrink-0 text-likely" />
          )}
          <p className="flex-1 text-[0.9375rem] font-semibold text-ink-800">
            {toast.message}
          </p>
          <button
            type="button"
            onClick={dismissToast}
            aria-label="알림 닫기"
            className="-mr-1 -mt-0.5 rounded-lg p-1 text-ink-500 transition-colors hover:bg-canvas-200 hover:text-ink-800 focus-ring"
          >
            <X aria-hidden="true" className="h-4 w-4" />
          </button>
        </div>
      )}
    </div>
  );
}
