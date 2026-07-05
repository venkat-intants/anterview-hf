// Typed toast helpers — wraps sonner v2 so callers import from one place.
// Usage: import { toast } from '@/lib/toast'
//        toast.success('Saved!') | toast.error('Failed') | toast.info('...') | toast.promise(...)
import { toast as sonnerToast, type ExternalToast } from 'sonner';

export const toast = {
  success(message: string, opts?: ExternalToast): string | number {
    return sonnerToast.success(message, opts);
  },

  error(message: string, opts?: ExternalToast): string | number {
    return sonnerToast.error(message, opts);
  },

  info(message: string, opts?: ExternalToast): string | number {
    return sonnerToast.info(message, opts);
  },

  warning(message: string, opts?: ExternalToast): string | number {
    return sonnerToast.warning(message, opts);
  },

  // sonner v2: promise() takes (promise, data?) — data merges ExternalToast.
  // We delegate directly to avoid re-typing the complex overloaded return type.
  promise: sonnerToast.promise,

  dismiss(id?: string | number): void {
    sonnerToast.dismiss(id);
  },
} as const;
