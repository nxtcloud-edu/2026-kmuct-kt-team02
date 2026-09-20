import { useEffect } from "react";
import { useLocation } from "react-router-dom";

/** 경로가 바뀌면 화면 맨 위로 */
export function ScrollToTop() {
  const { pathname } = useLocation();
  useEffect(() => {
    window.scrollTo({ top: 0, behavior: "auto" });
  }, [pathname]);
  return null;
}
