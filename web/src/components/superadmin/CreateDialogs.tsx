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
import { AlertTriangle, KeyRound } from '@/design/components/icons';
import { errorMessage } from '@/lib/errorMessage';

/** The text to show for a failed create, or null while there is no failure. */
function errorText(error: unknown, fallback = 'Could not create the account.'): string | null {
  if (error == null) return null;
  return errorMessage(error, fallback);
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

function ServerError({ error, fallback }: { error: unknown; fallback?: string }) {
  const message = errorText(error, fallback);
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
      <ServerError error={error} fallback="Could not create the company." />
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

/* ─────────────────────────────  Delete company  ───────────────────────────── */

interface DeleteCompanyDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  companyName: string;
  /** Logins the delete removes that the console knows about, for the warning. */
  memberSummary: string;
  pending: boolean;
  onConfirm: () => void;
}

/**
 * Deleting a tenant removes every login in it, revokes their sessions and makes
 * all of its hiring data unreachable (audit AD-F13). That is the heaviest action
 * in the product, so it is a dialog that states the consequences and only arms
 * once the company's name is typed, not the inline "Sure?" used for one row.
 */
export function DeleteCompanyDialog({
  open,
  onOpenChange,
  companyName,
  memberSummary,
  pending,
  onConfirm,
}: DeleteCompanyDialogProps) {
  return (
    <DialogShell
      open={open}
      onOpenChange={onOpenChange}
      pending={pending}
      title={`Delete ${companyName}?`}
      description="This cannot be undone from the console."
    >
      <DeleteCompanyForm
        companyName={companyName}
        memberSummary={memberSummary}
        pending={pending}
        onConfirm={onConfirm}
        onCancel={() => onOpenChange(false)}
      />
    </DialogShell>
  );
}

function DeleteCompanyForm({
  companyName,
  memberSummary,
  pending,
  onConfirm,
  onCancel,
}: Omit<DeleteCompanyDialogProps, 'open' | 'onOpenChange'> & { onCancel: () => void }) {
  const [typed, setTyped] = useState('');
  const matches = typed.trim() === companyName.trim();

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault();
    if (matches && !pending) onConfirm();
  };

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      <div className="flex items-start gap-2.5 rounded-[12px] border border-[var(--ui-danger)]/35 bg-[var(--ui-danger-wash)] p-3 text-[13px] leading-relaxed text-foreground">
        <AlertTriangle
          className="mt-0.5 h-4 w-4 flex-none text-[var(--ui-danger)]"
          aria-hidden="true"
        />
        <ul className="list-disc space-y-1 pl-4">
          <li>Every account in this company is removed ({memberSummary}).</li>
          <li>Everyone signed in to it is signed out straight away.</li>
          <li>Its openings, applicants, exams and interviews can no longer be opened.</li>
        </ul>
      </div>
      <Field
        id="delete-company-confirm"
        label={`Type ${companyName} to confirm`}
        value={typed}
        onChange={(e) => setTyped(e.target.value)}
        autoComplete="off"
        disabled={pending}
      />
      <div className="flex flex-col-reverse gap-2 border-t border-border pt-4 sm:flex-row sm:justify-end">
        <Button type="button" variant="outline" disabled={pending} onClick={onCancel}>
          Cancel
        </Button>
        <Button
          type="submit"
          variant="destructive"
          disabled={!matches || pending}
          aria-busy={pending}
        >
          {pending ? 'Deleting…' : 'Delete company'}
        </Button>
      </div>
    </form>
  );
}
