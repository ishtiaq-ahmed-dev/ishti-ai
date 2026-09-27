import { Component, type ErrorInfo, type ReactNode } from "react";
import { AlertTriangle, RefreshCw, Home } from "lucide-react";

interface Props {
  children: ReactNode;
}

interface State {
  hasError: boolean;
  error: Error | null;
  errorInfo: ErrorInfo | null;
}

export class ErrorBoundary extends Component<Props, State> {
  public state: State = {
    hasError: false,
    error: null,
    errorInfo: null,
  };

  public static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error, errorInfo: null };
  }

  public componentDidCatch(error: Error, errorInfo: ErrorInfo) {
    console.error("[ISHTI ErrorBoundary caught error]:", error, errorInfo);
    this.setState({ errorInfo });
  }

  private handleReload = () => {
    window.location.reload();
  };

  private handleReset = () => {
    try {
      localStorage.removeItem("ishti-page");
      window.location.hash = "#chat";
    } catch {}
    window.location.reload();
  };

  public render() {
    if (this.state.hasError) {
      return (
        <div className="flex h-screen w-screen items-center justify-center bg-canvas p-6 text-foreground font-sans">
          <div className="relative w-full max-w-lg overflow-hidden rounded-3xl border border-border bg-card p-6 shadow-2xl">
            <div className="flex items-center gap-3 text-danger mb-4">
              <div className="grid h-10 w-10 place-items-center rounded-2xl bg-danger/15 text-danger">
                <AlertTriangle className="h-5 w-5" />
              </div>
              <div>
                <h2 className="text-base font-semibold">Something went wrong</h2>
                <p className="text-xs text-muted-foreground">ISHTI encountered an unexpected interface error.</p>
              </div>
            </div>

            <div className="mb-6 rounded-2xl border border-border/80 bg-background/80 p-3.5 text-xs font-mono text-muted-foreground max-h-48 overflow-y-auto scroll-thin">
              <p className="font-semibold text-foreground mb-1">
                {this.state.error?.name}: {this.state.error?.message}
              </p>
              {this.state.error?.stack && (
                <pre className="text-[11px] leading-relaxed opacity-75 whitespace-pre-wrap break-all">
                  {this.state.error.stack.split("\n").slice(1, 6).join("\n")}
                </pre>
              )}
            </div>

            <div className="flex flex-wrap items-center justify-end gap-2.5">
              <button
                type="button"
                onClick={this.handleReset}
                className="flex items-center gap-1.5 rounded-xl border border-border bg-muted/60 px-3.5 py-2 text-xs font-medium text-foreground hover:bg-muted transition-colors cursor-pointer"
              >
                <Home className="h-3.5 w-3.5 text-muted-foreground" />
                <span>Return to Home</span>
              </button>
              <button
                type="button"
                onClick={this.handleReload}
                className="flex items-center gap-1.5 rounded-xl bg-accent px-4 py-2 text-xs font-semibold text-white hover:opacity-90 transition-opacity cursor-pointer shadow-xs"
              >
                <RefreshCw className="h-3.5 w-3.5" />
                <span>Reload App</span>
              </button>
            </div>
          </div>
        </div>
      );
    }

    return this.props.children;
  }
}
