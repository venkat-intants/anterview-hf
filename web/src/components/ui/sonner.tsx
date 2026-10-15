// Thin wrapper that mounts the sonner Toaster with project-wide defaults.
// Import this once at the app root — all other code uses @/lib/toast for triggers.
import { Toaster as SonnerToaster } from 'sonner';

type ToasterProps = React.ComponentProps<typeof SonnerToaster>;

const Toaster = ({ ...props }: ToasterProps) => {
  return (
    <SonnerToaster
      position="top-right"
      // `toaster group` is what the group-[.toaster] class names below key off.
      // pointer-events-auto keeps a toast clickable while a Radix dialog is open:
      // Radix sets pointer-events:none on <body> and the toaster inherits it, so
      // an error toast raised from inside a dialog could not be dismissed.
      className="toaster group pointer-events-auto"
      richColors
      closeButton
      toastOptions={{
        classNames: {
          toast:
            'group toast group-[.toaster]:bg-popover group-[.toaster]:text-foreground group-[.toaster]:border-border group-[.toaster]:shadow-elevated group-[.toaster]:rounded-[14px]',
          description: 'group-[.toast]:text-muted-foreground',
          actionButton: 'group-[.toast]:bg-primary group-[.toast]:text-primary-foreground',
          cancelButton: 'group-[.toast]:bg-muted group-[.toast]:text-muted-foreground',
        },
      }}
      {...props}
    />
  );
};

export { Toaster };
