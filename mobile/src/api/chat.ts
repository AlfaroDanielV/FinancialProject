import { api } from "./client";

export interface ChatButton {
  label: string;
  callback_data: string;
}

export interface ChatUrlButton {
  label: string;
  url: string;
}

export interface ChatMessageResponse {
  reply_text: string;
  buttons: ChatButton[];
  url_buttons: ChatUrlButton[];
}

export async function postChatMessage(text: string): Promise<ChatMessageResponse> {
  const { data } = await api.post<ChatMessageResponse>("/chat/message", { text });
  return data;
}

export async function postChatImage(
  uri: string,
  mediaType: string,
): Promise<ChatMessageResponse> {
  const form = new FormData();
  const filename = uri.split("/").pop() ?? "receipt.jpg";
  // React Native FormData accepts the object shape {uri, type, name}
  form.append("file", { uri, type: mediaType, name: filename } as unknown as Blob);
  const { data } = await api.post<ChatMessageResponse>("/chat/image", form, {
    headers: { "Content-Type": "multipart/form-data" },
  });
  return data;
}
