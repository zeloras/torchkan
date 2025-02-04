import torch
import torch.nn as nn
import torch.nn.functional as F

class KAN(nn.Module):
    def __init__(self, layers_hidden, grid_size=5, spline_order=3, base_activation=nn.GELU, grid_range=[-1, 1]):
        super(KAN, self).__init__()
        self.layers_hidden = layers_hidden
        self.grid_size = grid_size
        self.spline_order = spline_order
        self.base_activation = base_activation()  # Instantiating the base activation function
        self.grid_range = grid_range

        # Initialize the model's parameters and layer norms
        self.base_weights = nn.ParameterList()
        self.spline_weights = nn.ParameterList()
        self.layer_norms = nn.ModuleList()
        self.prelus = nn.ModuleList()  # PReLU for learning non-linearity
        self.grids = []

        for i, (in_features, out_features) in enumerate(zip(layers_hidden, layers_hidden[1:])):
            self.base_weights.append(nn.Parameter(torch.randn(out_features, in_features)))
            self.spline_weights.append(nn.Parameter(torch.randn(out_features, in_features, grid_size + spline_order)))
            self.layer_norms.append(nn.LayerNorm(out_features))
            self.prelus.append(nn.PReLU())  # Adding a PReLU activation for each layer

            h = (self.grid_range[1] - self.grid_range[0]) / grid_size
            grid = torch.linspace(
                self.grid_range[0] - h * spline_order,
                self.grid_range[1] + h * spline_order,
                grid_size + 2 * spline_order + 1,
                dtype=torch.float32
            ).expand(in_features, -1).contiguous()
            self.register_buffer(f'grid_{i}', grid)
            self.grids.append(grid)

        # Kaiming uniform initialization
        for weight in self.base_weights:
            nn.init.kaiming_uniform_(weight, nonlinearity='linear')
        for weight in self.spline_weights:
            nn.init.kaiming_uniform_(weight, nonlinearity='linear')

    def forward(self, x):
        for i, (base_weight, spline_weight, layer_norm, prelu) in enumerate(zip(self.base_weights, self.spline_weights, self.layer_norms, self.prelus)):
            grid = self._buffers[f'grid_{i}']
            x = x.to(base_weight.device)

            # Base model computation
            base_output = F.linear(self.base_activation(x), base_weight)
            x_uns = x.unsqueeze(-1)
            bases = ((x_uns >= grid[:, :-1]) & (x_uns < grid[:, 1:])).to(x.dtype)

            for k in range(1, self.spline_order + 1):
                left_intervals = grid[:, :-(k + 1)]
                right_intervals = grid[:, k:-1]
                delta = torch.where(right_intervals == left_intervals, torch.ones_like(right_intervals), right_intervals - left_intervals)
                bases = ((x_uns - left_intervals) / delta * bases[:, :, :-1]) + \
                        ((grid[:, k + 1:] - x_uns) / (grid[:, k + 1:] - grid[:, 1:(-k)]) * bases[:, :, 1:])
            bases = bases.contiguous()

            # Spline output computation
            spline_output = F.linear(bases.view(x.size(0), -1), spline_weight.view(spline_weight.size(0), -1))
            x = prelu(layer_norm(base_output + spline_output)) # Observed to make the training stable with less reliance on the input weights

        return x