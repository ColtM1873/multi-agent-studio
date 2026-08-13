"""通用 prompt 常量（与 various_prompts.py 一致，作为 app 包自包含副本）。"""

summery_promt = f"""
<primary_objective>
Extract the highest quality/most relevant context from the entire conversation history.
上下文长度即将超过限制。现在需要将已有的全部历史对话进行总结。
总结完毕后除了会保存最近的一到两轮会话，所有历史会话记录都会被清除。本次生成的总结和已经保存在记忆里面的内容将继续存留。
</primary_objective>

<objective_information>
You're nearing the total number of input tokens you can accept, so you must extract the highest quality/most relevant pieces of information from your conversation history.
This context will then take the place of the entire conversation history. Because of this, ensure the context you extract is only the most important information to continue working toward your overall goal.
</objective_information>

<instructions>
You want to ensure that you don't repeat any actions you've already completed, so the context you extract from the conversation history should be focused on the most important information to your overall goal.

You should structure your summary using the following sections:

## SESSION INTENT

What is the user's primary goal or request? What overall task are you trying to accomplish? This should be concise but complete enough to understand the purpose of the entire session.

## SUMMARY

Extract and record all of the most important context from the conversation history. Include important choices, conclusions, or strategies determined during this conversation. Include the reasoning behind key decisions. Document any rejected options and why they were not pursued.

## ARTIFACTS

What artifacts, files, or resources were created, modified, or accessed during this conversation? For file modifications, list specific file paths and briefly describe the changes made to each. This section prevents silent loss of artifact information.

## NEXT STEPS

What specific tasks remain to be completed to achieve the session intent? What should you do next?

</instructions>

Please carefully read over the entire conversation history, and extract the most important and relevant context to replace it so that you can free up space in the conversation history.
Respond ONLY with the extracted context. Do not include any additional information, or text before or after the extracted context.

鉴于整个会话长度此时已经达到了几十万token，因此请在总结时不要忌惮长度太长。输出大篇幅的总结是被鼓励的，否则不足以覆盖如此长的会话历史。具体来说，数万token长度的总结都是可以接受的。
另外##注意##，你在本次回答中不应当调用任何的工具。
"""


ReAct_system_prompt = """
## Reasoning Protocol
Before each tool call, you should:
1. **Analyze**: What do I know? What do I need to know?  
2. **Plan**: What tool(s) will help me get the missing information?
3. **Execute**: Call the tool with appropriate parameters.
4. **Reflect**: After receiving tool output, evaluate — did I get what I needed? 
   Is the information sufficient? If not, what's my next step?
5. **Decide**: Continue gathering information, or synthesize final answer?

## Important
- NEVER call a tool without first reasoning about what you need
- ALWAYS evaluate tool results before deciding next steps
- For complex multi-step tasks, break them down explicitly
- If a tool returns unexpected results, explain your revised plan before trying again
"""
