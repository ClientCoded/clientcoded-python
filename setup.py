from setuptools import setup, find_packages

setup(
    name="clientcoded",
    version="0.2.0",
    description="ClientCoded SDK - Trace your AI agent's tool calls for root cause analysis",
    long_description=open("README.md").read(),
    long_description_content_type="text/markdown",
    author="ClientCoded",
    author_email="travis@clientcoded.com",
    url="https://github.com/ClientCoded/clientcoded-python",
    packages=find_packages(),
    install_requires=["requests>=2.20.0"],
    python_requires=">=3.8",
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Programming Language :: Python :: 3",
        "Topic :: Software Development :: Testing",
    ],
    keywords="ai agent testing qa tracing evaluation langchain openai anthropic",
)
