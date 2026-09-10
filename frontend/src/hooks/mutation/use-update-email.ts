import { useMutation } from "@tanstack/react-query";
import { emailService } from "#/api/email-service/email-service.api";

export const useUpdateEmail = () =>
  useMutation({ mutationFn: emailService.updateEmail });
