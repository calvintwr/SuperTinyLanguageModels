"""Integration code for LLM Harness."""

import torch
from lm_eval import evaluator, tasks
from lm_eval.api import instance, model
from tqdm import tqdm

from evals import evaluator_interface
from models import model_shell

def batch(data, batch_size):
    for i in range(0, len(data), batch_size):
        yield data[i : i + batch_size]

class LMEvalWrappedModel(model.LM):
    def __init__(self, model_shell: model_shell.ModelShell):
        self.model_shell = model_shell
        super().__init__()



    def loglikelihood(self, requests: list[instance.Instance]) -> list[tuple[float, bool]]:
        """
        Each request contains Instance.args : Tuple[str, str] containing
        1. an input string to the LM
        2. a target string on which the loglikelihood of the LM producing this target,
        conditioned on the input, will be returned.

        Each request will have, as result, (ll, is_greedy):
        Tuple[float, int] returned, where:
        ll is a floating point number representing the log probability of generating
        the target string conditioned on the input,
        is_greedy being either the value 0 or 1,
        with it being 1 if and only if the target string would be generated by greedy sampling
        from the LM (that is, if the target string is the most likely N-token string
        to be output by the LM given the input.)
        """
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.model_shell = self.model_shell.to(device)

        results = []
        for batch_requests in tqdm(batch(requests, batch_size=32)):
            with torch.cuda.amp.autocast():
                context_strs = [request.args[0] for request in batch_requests]
                target_strs = [request.args[1] for request in batch_requests]

                # tokenize the inputs
                context_tokens = [self.model_shell.embedding_model.tokenize_input(context_str) for context_str in context_strs]
                target_tokens = [self.model_shell.embedding_model.tokenize_input(target_str) for target_str in target_strs]

                # append the target tokens to the input tokens
                unpadded_input_tokens = [
                    (context_tokens[i] + target_tokens[i])[:-1][-512:]  # we don't want to include the final token since tokens are shifted by 1
                    for i in range(len(context_tokens))
                ]
                # pad the input tokens to the max length in the batch
                pad_token = self.model_shell.embedding_model.tokenizer.pad_token
                max_len = max(len(tokens) for tokens in unpadded_input_tokens)
                input_tokens = [
                    tokens + [pad_token] * (max_len - len(tokens)) for tokens in unpadded_input_tokens
                ]

                input_tokens = torch.tensor(input_tokens, device=device).long()

                # get the logits
                logits, _ = self.model_shell(input_tokens)


                for i, _ in enumerate(batch_requests):
                    logits_i = logits[i]
                    # remove the padding
                    logits_i = logits_i[: len(unpadded_input_tokens[i])]
                    target_tokens_i = torch.tensor(target_tokens[i], device=device).long()

                    # get the loglikelihood of the target string
                    ll = torch.nn.functional.cross_entropy(
                        logits_i[-len(target_tokens_i):], target_tokens_i, reduction="sum"
                    )

                    # get the greedy prediction
                    greedy_prediction = torch.argmax(logits_i[:-len(target_tokens_i)], dim=-1)
                    is_greedy = torch.equal(greedy_prediction, target_tokens_i)

                    results.append((ll.item(), is_greedy))

        return results

    def loglikelihood_rolling(
        self, requests: list[instance.Instance]
    ) -> list[float, bool]:
        """
        Each request contains Instance.args : Tuple[str], which is an input string to the model whose entire loglikelihood, conditioned on purely the EOT token, will be calculated.
        This is used to evaluate perplexity on a data distribution.
        It should return (ll,) : Tuple[float] , a.k.a. solely the loglikelihood of producing each piece of text given no starting input.
        """
        for request in requests:
            (_str,) = request.args
            # tokenize the inputs
            input_tokens = self.model_shell.embedding_model.tokenize_input(_str)
            input_tokens = (
                self.model_shell.embedding_model.tokenizer.eot_token + input_tokens[:-1]
            )[-512:]
            input_tokens = torch.tensor(input_tokens)
            # get the logits
            logits, _ = self.model_shell(input_tokens.unsqueeze(0))
            logits = logits.squeeze()
             # only use the last 512 tokens
            # get the loglikelihood of the target string
            ll = torch.nn.functional.cross_entropy(
                logits, input_tokens, reduction="sum"
            ).item()
            yield ll

    def generate_until(self, requests: list[instance.Instance]) -> list[str]:
        """
        Each request contains Instance.args : Tuple[str, dict] containing:
            1. an input string to the LM
            2. a dictionary of keyword arguments used to control generation parameters.
        Using this input and these generation parameters,
            text will be sampled from the language model
            (typically until a maximum output length or specific stopping string sequences
            --for example, {"until": ["\n\n", "."], "max_gen_toks": 128}).
        The generated input+output text from the model will then be returned.
        """
        for request in requests:
            context_str, gen_kwargs = request.args
            # tokenize the inputs
            context_tokens = self.model_shell.embedding_model.tokenize_input(
                context_str
            )
            # get the logits
            logits = self.model_shell(context_tokens)
            # generate the text
            generated_text = self.model_shell.model_head.generate_text(
                logits, **gen_kwargs
            )
            yield context_str + generated_text


class LLMHarness(evaluator_interface.EvaluationInterface):
    """Uses LLM harness to evaluate a model."""

    def __init__(self, base_model: model_shell.ModelShell):
        self.base_model = base_model
        self.lm = LMEvalWrappedModel(base_model)

    def evaluate(self, benchmark_names: list[str]):
        """
        Evaluate the model performance on a specific benchmark
        """
        task_manager = tasks.TaskManager()
        results = evaluator.simple_evaluate(
            model=self.lm,
            task_manager=task_manager,
            tasks=benchmark_names,
            num_fewshot=5,
        )
        print(results["results"])
        return results
