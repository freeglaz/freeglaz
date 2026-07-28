import { Component } from 'react';
import { AlertTriangle } from 'lucide-react';

/**
 * React safety net: catches a render exception in its subtree and shows a
 * localized message instead of a fully gray screen.
 *
 * Does NOT replace fixing the root cause — it is a safety belt so a future
 * exception stays contained and readable.
 *
 * `label`: displayed text (already translated by the caller, no i18n hook here
 * since a class component has no access to useTranslation).
 */
export default class ErrorBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { error: null };
  }

  static getDerivedStateFromError(error) {
    return { error };
  }

  componentDidCatch(error, info) {
    console.error('ErrorBoundary caught:', error, info);
  }

  render() {
    if (this.state.error) {
      return (
        <div className="p-4 flex items-start gap-2 text-icc-warn text-xs2"
             role="alert">
          <AlertTriangle size={14} className="mt-0.5 shrink-0"/>
          <div className="min-w-0">
            <div className="font-medium">{this.props.label}</div>
            <div className="font-mono text-tiny text-text-faint break-all mt-1">
              {String(this.state.error?.message || this.state.error)}
            </div>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}
