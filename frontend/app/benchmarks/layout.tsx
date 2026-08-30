import type { ReactNode } from "react";
import styles from "./benchmarks.module.css";

export default function BenchmarksLayout({ children }: { children: ReactNode }) {
  return <div className={styles.page}>{children}</div>;
}
