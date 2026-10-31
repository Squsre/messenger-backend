"use client";

import { use } from "react";
import { ChatViewport } from "@/components/chat/chat-viewport";

export default function GroupChatPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const chatId = Number(id);
  return <ChatViewport chatType="group" chatId={chatId} />;
}
