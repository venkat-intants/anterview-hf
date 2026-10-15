// The bank setup form's Zod schema (react-hook-form resolves it). The API holds
// a name and a description and nothing else about a bank, so that is all there is.

import { z } from 'zod';

export const bankSetupSchema = z.object({
  name: z
    .string()
    .trim()
    .min(1, 'Give the bank a name.')
    .max(200, 'Keep the name under 200 characters.'),
  description: z.string().trim().max(2000, 'Keep this under 2,000 characters.'),
});

export type BankSetupValues = z.infer<typeof bankSetupSchema>;
