FROM public.ecr.aws/lambda/python:3.12

# Copy requirements first
COPY requirements.txt .

# Install dependencies
RUN pip install -r requirements.txt --target "${LAMBDA_TASK_ROOT}"

# Copy function code
COPY app.py ${LAMBDA_TASK_ROOT}

# Set handler
CMD [ "app.lambda_handler" ]