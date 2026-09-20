import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

/** Tailwind 클래스 병합. 뒤에 오는 클래스가 같은 속성을 덮어쓴다. */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}
