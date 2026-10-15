// Create-account and create-company dialogs for the two admin consoles.
//
// The form state lives in the dialog's body, which Radix unmounts on close, so
// Cancel (or Escape, or a click outside) discards whatever was typed. The dialog
// never closes itself: the caller closes it from the mutation's onSuccess, so a
// failed create keeps the typed values and shows the server's reason inline.
// The caller also keeps the "set-password link was emailed" outcome (toast +
// the page's own hint) outside the dialog, where it stays visible after close.

import { useState, type FormEvent, type ReactNode } from 'react';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Field } from '@/design/components/primitives';
import { KeyRound } from '@/design/components/icons';

/** The text to show for a failed create, or null while there is no failure. */
function errorText(error: unknown): string | null {
  if (error == null) return null;
  return error instanceof Error ? error.message : 'Something went wrong. Please try again.';
}

interface ShellProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  pending: boolean;
  title: string;
  description: string;
  children: ReactNode;
}

function DialogShell({ open, onOpenChange, pending, title, description, children }: ShellProps) {
  return (
    <Dialog
      open={open}
      // A create in flight cannot be cancelled, so the dialog stays put until it settles.
      onOpenChange={(next) => {
        if (!next && pending) return;
        onOpenChange(next);
      }}
    >
      <DialogContent>
        <DialogHeader>
          <DialogTitle className="text-[18px] font-bold leading-snug">{title}</DialogTitle>
          <DialogDescription className="text-[13.5px] leading-relaxed">
            {description}
          </DialogDescription>
        </DialogHeader>
        {children}
      </DialogContent>
    </Dialog>
  );
}

function FormFooter({
  pending,
  submitLabel,
  onCancel,
}: {
  pending: boolean;
  submitLabel: string;
  onCancel: () => void;
}) {
  return (
    <div className="flex flex-col-reverse gap-2 border-t border-border pt-4 sm:flex-row sm:justify-end">
      <Button type="button" variant="outline" disabled={pending} onClick={onCancel}>
        Cancel
      </Button>
      <Button type="submit" disabled={pending} aria-busy={pending}>
        {pending ? 'Adding…' : submitLabel}
      </Button>
    </div>
  );
}

function ServerError({ error }: { error: unknown }) {
  const message = errorText(error);
  if (!message) return null;
  return (
    <p
      role="alert"
      className="rounded-[12px] bg-[var(--ui-danger-wash)] px-3.5 py-2.5 text-[13.5px] text-[var(--ui-danger)]"
    >
      {message}
    </p>
  );
}

/* ─────────────────────────────  Account (email + name)  ───────────────────────────── */

interface AccountDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Prefix for the field ids, e.g. `hr` gives `hr-email` and `hr-name`. */
  idPrefix: string;
  title: string;
  description: string;
  submitLabel: string;
  emailPlaceholder: string;
  namePlaceholder: string;
  pending: boolean;
  /** The last failed create's error (null when there is none), shown inline. */
  error: unknown;
  onSubmit: (values: { email: string; fullName: string }) => void;
}

function AccountForm({
  idPrefix,
  submitLabel,
  emailPlaceholder,
  namePlaceholder,
  pending,
  error,
  onSubmit,
  onCancel,
}: Omit<AccountDialogProps, 'open' | 'onOpenChange' | 'title' | 'description'> & {
  onCancel: () => void;
}) {
  const [email, setEmail] = useState('');
  const [fullName, setFullName] = useState('');
  const [touched, setTouched] = useState(false);

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault();
    if (!email.trim() || !fullName.trim()) {
      setTouched(true);
      return;
    }
    onSubmit({ email, fullName });
  };

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      <p className="text-[12.5px] text-muted-foreground">Both fields are required.</p>
      <Field
        id={`${idPrefix}-email`}
        label="Email"
        type="email"
        value={email}
        onChange={(e) => setEmail(e.target.value)}
        placeholder={emailPlaceholder}
        aria-required="true"
        autoComplete="off"
        disabled={pending}
        error={touched && !email.trim() ? 'Email is required.' : undefined}
      />
      <Field
        id={`${idPrefix}-name`}
        label="Full name"
        value={fullName}
        onChange={(e) => setFullName(e.target.value)}
        placeholder={namePlaceholder}
        aria-required="true"
        autoComplete="off"
        disabled={pending}
        error={touched && !fullName.trim() ? 'Full name is required.' : undefined}
      />
      <p className="flex items-start gap-2 text-[13px] leading-relaxed text-muted-foreground">
        <KeyRound
          className="mt-0.5 h-3.5 w-3.5 flex-none text-[var(--ui-info)]"
          aria-hidden="true"
        />
        They get an email with a link to choose their own password. No password is shared here.
      </p>
      <ServerError error={error} />
      <FormFooter pending={pending} submitLabel={submitLabel} onCancel={onCancel} />
    </form>
  );
}

export function AccountDialog({
  open,
  onOpenChange,
  title,
  description,
  pending,
  ...form
}: AccountDialogProps) {
  return (
    <DialogShell
      open={open}
      onOpenChange={onOpenChange}
      pending={pending}
      title={title}
      description={description}
    >
      <AccountForm pending={pending} {...form} onCancel={() => onOpenChange(false)} />
    </DialogShell>
  );
}

/* ─────────────────────────────  Company (name)  ───────────────────────────── */

interface CompanyDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  pending: boolean;
  error: unknown;
  onSubmit: (name: string) => void;
}

function CompanyForm({
  pending,
  error,
  onSubmit,
  onCancel,
}: Omit<CompanyDialogProps, 'open' | 'onOpenChange'> & { onCancel: () => void }) {
  const [name, setName] = useState('');
  const [touched, setTouched] = useState(false);

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault();
    if (!name.trim()) {
      setTouched(true);
      return;
    }
    onSubmit(name);
  };

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      <Field
        id="new-company-input"
        label="Company name"
        value={name}
        onChange={(e) => setName(e.target.value)}
        placeholder="Acme Skills University"
        aria-required="true"
        autoComplete="off"
        disabled={pending}
        error={touched && !name.trim() ? 'Company name is required.' : undefined}
      />
      <ServerError error={error} />
      <FormFooter pending={pending} submitLabel="Create company" onCancel={onCancel} />
    </form>
  );
}

export function CompanyDialog({
  open,
  onOpenChange,
  pending,
  error,
  onSubmit,
}: CompanyDialogProps) {
  return (
    <DialogShell
      open={open}
      onOpenChange={onOpenChange}
      pending={pending}
      title="New company"
      description="Add a company to the platform. You can create its super admin once it exists."
    >
      <CompanyForm
        pending={pending}
        error={error}
        onSubmit={onSubmit}
        onCancel={() => onOpenChange(false)}
      />
    </DialogShell>
  );
}
