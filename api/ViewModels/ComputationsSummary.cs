using System;
using System.Collections.Generic;
using System.Linq;
using System.Threading.Tasks;

namespace Mole.API.ViewModels
{
    public class ComputationsSummary
    {
        public string Date { get; set; }
        public int Count { get; set; }
        public IEnumerable<Summary> Summaries { get; set; }
    }

    public class Summary {
        public string Id { get; set; }
        public string Structure { get; set; }
        public int Computations { get; set; }
    }
}
